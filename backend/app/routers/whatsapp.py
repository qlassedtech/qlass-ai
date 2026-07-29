import logging
from contextlib import nullcontext

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.core import Student, ChatHistory, TopicProgress, ProcessedWebhookMessage, Quiz, Question, Answer
from app.services.whatsapp_client import (
    parse_incoming_message,
    parse_incoming_audio,
    parse_incoming_image,
    parse_incoming_document,
    guess_filename_from_media_url,
    download_media,
    send_whatsapp_message,
    send_whatsapp_audio,
    send_whatsapp_image,
    verify_webhook_auth,
)
from app.services.profile_builder import (
    next_missing_field,
    should_ask_this_turn,
    clean_answer,
    looks_like_answer,
    looks_like_confirmation_reply,
)
from app.services.sarvam_client import transcribe_audio, synthesize_speech, translate_text
from app.services.llm_client import translate_with_claude
from app.services.audio_qa import get_duration_seconds, detect_gender_from_pitch
from app.services.ocr_client import extract_text_from_image
from app.services.image_client import generate_image
from app.services.document_client import extract_text_from_document
from app.services.youtube_client import find_best_video
from app.services import cost_tracker
from app.services.habit import evaluate_habit_milestones
from app.services.rate_limit import is_rate_limited, student_lock
from app.services.tenancy import get_qlass_direct_centre_id
from app.services.referral import (
    generate_referral_code,
    extract_referral_code,
    looks_like_referral_request,
    evaluate_referral_milestones,
    REFERRAL_SIGNUP_BONUS,
)
from app.services.active_profile import (
    looks_like_new_profile_request,
    extract_switch_target_name,
    get_active_profile,
    set_active_profile,
    build_disambiguation_prompt,
    match_student_by_name,
)
from app.services.progress_report import (
    looks_like_progress_request,
    get_student_stats,
    get_activity_stats,
    get_welcome_back_note,
    get_chapter_coverage,
    format_progress_message,
)
from app.services.quiz_service import (
    extract_quiz_topic,
    looks_like_quiz_stop,
    looks_like_quiz_skip,
    generate_quiz_questions,
    grade_answer,
    QUIZ_QUESTION_COUNT,
)
from app.agents.tutor_agent import TutorAgent

logger = logging.getLogger(__name__)

router = APIRouter()
tutor_agent = TutorAgent()

HISTORY_TURNS = 12  # ~6 back-and-forth exchanges of prior context
WEAK_TOPICS_LIMIT = 5
OFF_LEVEL_SUGGEST_THRESHOLD = 3  # consecutive off-level questions before suggesting a class update

# A message this long is almost certainly a multi-question problem set (a
# pasted homework list, an OCR'd photo of one, or an extracted PDF/Word
# document) rather than a single question — pin it as the student's active
# document (see Student.active_document_text) so later turns like "now Q5"
# still work once the original message has scrolled out of HISTORY_TURNS.
ACTIVE_DOCUMENT_MIN_CHARS = 400
# Upper bound on what actually gets pinned — confirmed live that a 33,000-
# char paste got stored with no cap at all, permanently bloating that
# student's system prompt (and cost) for the rest of the session. 8,000
# chars comfortably covers a real 20-30 question DPP/homework sheet while
# still bounding the worst case.
ACTIVE_DOCUMENT_MAX_CHARS = 8000

_AFFIRMATIVE_WORDS = {"yes", "yeah", "yep", "sure", "ok", "okay", "haan", "y", "please", "yup"}

# Numbers with full feature access during this demo phase (per-account
# feature configuration at real onboarding is still TODO — see
# _get_or_create_student). Everyone else gets a plain-text-only tutor: no
# voice/OCR/image-generation/document costs until they're actually
# provisioned, so a random or leaked number can't run up paid API spend.
FULL_ACCESS_PHONES = {"918789674434", "918460184666", "918252345266", "917978046402"}

# Opposite-gender voice: a female voice for a detected-male student, a male
# voice for a detected-female student. Speaker names are from Sarvam's
# bulbul:v3 roster. Detection is pitch-based (see audio_qa.detect_gender_
# from_pitch) — a coarse, admittedly error-prone heuristic accepted
# deliberately for a first pass rather than not offering it at all.
OPPOSITE_GENDER_SPEAKER = {"male": "priya", "female": "shubh"}


def _assistant_voice_gender(student: Student) -> str:
    """
    Which gender the tutor's own voice reads as — needed so a Hindi
    translation can use matching grammatically-gendered verb forms (Hindi
    conjugates first-person verbs by the speaker's gender), not just for
    picking the TTS speaker name. Falls back to the configured default
    speaker's gender when the student's own gender hasn't been detected yet.
    """
    speaker = OPPOSITE_GENDER_SPEAKER.get(student.gender, settings.sarvam_tts_speaker)
    return "male" if speaker == "shubh" else "female"


# Per-feature WEEKLY usage caps (no daily tracking at all) — the actual AI
# cost per voice-reply/diagram/video (see cost_tracker.PRICING) is high
# enough that unlimited usage isn't sustainable at a mass-market price;
# these are the lever that keeps a subscription price profitable. Counted
# against the existing credit_events ledger (each already writes a row
# tagged by service), not a separate counter. All soft: hitting one just
# skips that extra (falling back to plain text, which already stands on its
# own) rather than blocking the whole conversation — the actual hard stop
# is the monthly ₹ credit limit below, which covers the core text feature.
FEATURE_LIMITS = {
    "voice": {"services": ["sarvam_tts"], "period": "week", "max": 5, "label": "voice replies"},
    "image_generation": {"services": ["azure_image"], "period": "week", "max": 3, "label": "diagrams"},
    "youtube_videos": {"services": ["youtube_search", "youtube_search_overage"], "period": "week", "max": 5, "label": "videos"},
}

# Per-student monthly AI credit allowance — the hard stop for the core
# (text) tutoring feature. Tracked in ₹ actually spent (at the already-2x
# ledger rate), not a raw message count, since cost varies a lot per turn
# (a Hindi translation or an off-level question costs more than a short
# English answer) — capping by ₹ is the accurate version of "how much of
# this month's subscription value have they used."
MONTHLY_STUDENT_CREDIT_LIMIT = 100.0  # INR
USAGE_WARNING_FRACTIONS = [0.5, 0.9, 1.0]


def _weekly_usage_snapshot(db: Session, student: Student) -> dict[str, int]:
    """
    One query for every soft-capped service's usage this week, instead of a
    separate round-trip per feature (voice/image/video checks used to each
    hit the database independently — up to 3 queries per turn).
    """
    if student.phone in FULL_ACCESS_PHONES:
        return {}
    all_services = [service for cfg in FEATURE_LIMITS.values() for service in cfg["services"]]
    return cost_tracker.get_usage_counts_by_service(db, student.id, all_services, "week")


def _usage_status(student: Student, feature: str, weekly_counts: dict[str, int]) -> tuple[int, int, str]:
    cfg = FEATURE_LIMITS[feature]
    if student.phone in FULL_ACCESS_PHONES:
        # Caps exist to keep a customer subscription price profitable — they
        # don't apply to the team's own internal demo/testing numbers.
        return 0, cfg["max"], cfg["period"]
    used = sum(weekly_counts.get(service, 0) for service in cfg["services"])
    return used, cfg["max"], cfg["period"]


def _limit_reached_message(feature: str, used: int, limit: int, period: str) -> str:
    label = FEATURE_LIMITS[feature]["label"]
    return f"You've used all {limit} {label} for this week — check back next Monday!"


def _threshold_notice(feature: str, used: int, limit: int, period: str) -> str | None:
    label = FEATURE_LIMITS[feature]["label"]
    for fraction in USAGE_WARNING_FRACTIONS:
        if used == round(limit * fraction):
            if fraction >= 1.0:
                return f"⚠️ That was your last {label[:-1] if label.endswith('s') else label} for this week — more available next Monday."
            return f"ℹ️ Heads up — you've used {used}/{limit} {label} for this week ({round(fraction * 100)}%)."
    return None


def _monthly_spend_status(db: Session, student: Student) -> float:
    if student.phone in FULL_ACCESS_PHONES:
        return 0.0  # internal demo/testing numbers aren't metered against the customer credit limit
    return cost_tracker.get_student_monthly_spend(db, student.id)


def _monthly_limit_reached_message() -> str:
    return f"You've used all ₹{MONTHLY_STUDENT_CREDIT_LIMIT:.0f} of this month's AI credit — resets on the 1st of next month!"


def _monthly_threshold_notice(spent_before: float, spent_after: float) -> str | None:
    """
    Spend is a continuous ₹ amount, not an integer count, so it won't land
    exactly on a 50%/90% boundary — instead, check whether this turn's cost
    carried the running total *across* a threshold, and report the highest
    one crossed (covers an expensive single turn, e.g. voice, jumping past
    more than one threshold at once, so only one notice goes out per turn).
    """
    crossed = [f for f in USAGE_WARNING_FRACTIONS if spent_before < MONTHLY_STUDENT_CREDIT_LIMIT * f <= spent_after]
    if not crossed:
        return None
    fraction = max(crossed)
    if fraction >= 1.0:
        return _monthly_limit_reached_message()
    return (
        f"ℹ️ Heads up — you've used ₹{spent_after:.2f} of your ₹{MONTHLY_STUDENT_CREDIT_LIMIT:.0f} "
        f"monthly AI credit ({round(fraction * 100)}%)."
    )


def _looks_affirmative(text: str) -> bool:
    words = set(text.strip().lower().split())
    return bool(words & _AFFIRMATIVE_WORDS)


def _create_new_student(db: Session, from_phone: str, message_text: str = "") -> Student:
    features = (
        {"voice": True, "ocr": True, "image_generation": True, "documents": True, "youtube_videos": True}
        if from_phone in FULL_ACCESS_PHONES
        else {"voice": False, "ocr": False, "image_generation": False, "documents": False, "youtube_videos": False}
    )

    referred_by_id = None
    referral_code = extract_referral_code(message_text)
    if referral_code:
        referrer = db.query(Student).filter(Student.referral_code == referral_code).first()
        if referrer:
            referred_by_id = referrer.id

    student = Student(
        name="New Student", phone=from_phone, features=features,
        centre_id=get_qlass_direct_centre_id(db), referred_by_id=referred_by_id,
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    student.referral_code = generate_referral_code(student.id)
    db.commit()
    cost_tracker.add_trial_credits(db, student.id)
    if referred_by_id:
        # Signup itself is the first referral milestone — paid immediately,
        # not gated on any activity (see REFERRAL_MILESTONES for the rest,
        # which DO require the referred student to actually engage).
        cost_tracker.grant_referral_credit(
            db, referred_by_id, REFERRAL_SIGNUP_BONUS, note="Referral milestone: signup"
        )
    return student


async def _resolve_active_student(db: Session, from_phone: str, message_text: str) -> tuple[Student, str | None]:
    """
    Returns (student, early_reply). If early_reply is set, the caller should
    send it and stop instead of treating this message as a real tutoring
    question — covers two cases: we don't know which student is chatting
    yet (disambiguation) and we just created a fresh profile for a new
    sibling (which needs a "what's your name?" confirmation, not the LLM
    trying to answer what was really a "different child" trigger phrase).
    For the overwhelmingly common case (one profile per phone) this is a
    single query with zero extra overhead; the multi-profile path only
    kicks in for phones that actually have more than one student on them.
    """
    students = db.query(Student).filter(Student.phone == from_phone).order_by(Student.id).all()

    if not students:
        return _create_new_student(db, from_phone, message_text), None

    if looks_like_new_profile_request(message_text):
        new_student = _create_new_student(db, from_phone)
        await set_active_profile(from_phone, new_student.id)
        missing = next_missing_field(new_student)
        question = missing[1] if missing else "What's your name?"
        if missing:
            new_student.pending_profile_field = missing[0]
            db.commit()
        return new_student, f"Got it, setting up a new profile! {question}"

    if len(students) == 1:
        return students[0], None

    # Explicit switch request ("switch to Raj", "it's Priya now") overrides
    # any cached active profile — without this, once one sibling's session
    # is cached, the other has no way to take over except waiting out the
    # TTL or happening to mention their own name in an ordinary question.
    switch_target = extract_switch_target_name(message_text)
    if switch_target:
        match = next((s for s in students if s.name and s.name.lower() == switch_target.lower()), None)
        if match:
            await set_active_profile(from_phone, match.id)
            return match, f"Switched! Hey {match.name}, what can I help you with? 😊"
        # Named someone we don't have a profile for — don't silently create
        # one on a guess (typo risk); ask instead.
        existing_names = ", ".join(s.name for s in students if s.name)
        return students[0], (
            f"I don't have a profile for '{switch_target}' yet — I've got {existing_names} on this number. "
            f"Want me to set up a new profile instead?"
        )

    active_id = await get_active_profile(from_phone)
    if active_id:
        match = next((s for s in students if s.id == active_id), None)
        if match:
            return match, None

    name_match = match_student_by_name(students, message_text)
    if name_match:
        await set_active_profile(from_phone, name_match.id)
        return name_match, None

    prompt = build_disambiguation_prompt([s.name for s in students])
    return students[0], prompt


@router.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    """
    Wati -> ack immediately -> process in the background (parse text/voice/
    image/document -> find/create Student -> TutorAgent -> save chat_history
    -> send reply via Wati's API).

    The actual pipeline (STT/LLM/TTS/OCR/image-gen) can take 10+ seconds end
    to end — Wati's webhook caller times out well before that and marks the
    delivery "failed", retrying it even though we'd already generated and
    sent a real reply (confirmed in production: reply appeared in chat
    history ~12s after the message, but Wati still retried and our own
    dedup correctly rejected the retry as already-handled — the *retry* was
    spurious, not the original send). Repeated "failures" like this are also
    what's likely to get a webhook marked "Defective" by Wati and paused
    entirely. Acknowledging fast and doing the real work after the response
    is sent avoids both problems, since sending the reply is already a
    separate outbound call to Wati's API, not dependent on this response.

    Unlike Meta's Cloud API, Wati has no GET verification handshake — you just
    point Wati's dashboard webhook setting at this URL.
    """
    if not verify_webhook_auth(request.headers.get("authorization")):
        raise HTTPException(status_code=403, detail="Invalid webhook auth")

    try:
        payload = await request.json()
    except Exception:
        # Malformed/non-JSON body — Wati itself never sends one, but
        # anything reachable over the internet eventually gets a malformed
        # request, and this previously fell through as an unhandled 500.
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Guard against Wati redelivering/retrying a webhook call for a message
    # we already processed — mark it seen as early as possible, before any
    # real work, so a duplicate delivery is a no-op rather than a second
    # confusing reply to the student. This is a fast, single-insert check,
    # safe to do before acknowledging.
    webhook_message_id = payload.get("whatsappMessageId") or payload.get("id")
    if webhook_message_id:
        db = SessionLocal()
        try:
            db.add(ProcessedWebhookMessage(message_id=webhook_message_id))
            db.commit()
        except IntegrityError:
            db.rollback()
            return {"received": True, "handled": False, "reason": "duplicate webhook delivery, already processed"}
        finally:
            db.close()

    background_tasks.add_task(_process_webhook_payload, payload)
    return {"received": True, "handled": "processing"}


async def _process_webhook_payload(payload: dict) -> None:
    """
    The actual work, run after the webhook response has already been sent.
    Opens its own DB session since the request-scoped one from Depends(get_db)
    would already be closed by the time a background task runs.
    """
    phone = payload.get("waId")
    try:
        async with (student_lock(phone) if phone else nullcontext()):
            db = SessionLocal()
            try:
                await _handle_message(db, payload)
            finally:
                db.close()
    except Exception:
        # An uncaught exception here (including failing to even acquire the
        # per-student lock) previously meant the student got no reply at all
        # and we had no record of why — log the full traceback and let them
        # know something broke instead of leaving them hanging.
        logger.exception("Unhandled error processing webhook payload for %s", phone)
        if phone:
            await send_whatsapp_message(
                phone, "Sorry, something went wrong on my end — please try sending that again."
            )


async def _handle_message(db: Session, payload: dict) -> None:
    # Whether to reply with synthesized speech. Deliberately NOT set just
    # because the student's own input happened to be a voice note — a
    # student who spoke their question may still prefer to read the answer.
    # Only the tutor's explicit "audio=true" decision (student asked for a
    # voice reply) turns this on, further down.
    send_voice_reply = False

    from_phone = payload.get("waId")
    if not from_phone:
        return

    if await is_rate_limited(from_phone):
        await send_whatsapp_message(
            from_phone, "You're sending messages a bit fast — please wait a moment before sending more."
        )
        return

    # Parsed before resolving which student profile is active, so a shared
    # family phone with multiple profiles can be disambiguated by matching
    # a name mentioned in this text (see _resolve_active_student). For
    # audio/image/document messages the text isn't known yet at this point
    # (needs downloading/transcribing/OCR first), so name-matching only
    # applies to plain text messages — that's an acceptable gap: someone on
    # an ambiguous shared phone would still get asked "who's this?" before
    # a voice/photo message is processed.
    parsed = parse_incoming_message(payload)
    probe_text = parsed[1] if parsed else ""
    student, early_reply = await _resolve_active_student(db, from_phone, probe_text)
    if early_reply:
        await send_whatsapp_message(from_phone, early_reply)
        return

    # Each student has their own wallet now (see cost_tracker) — checked
    # here, after resolving which student this actually is, rather than
    # against one shared account-wide balance. Demo/testing numbers get
    # unlimited credits (see FULL_ACCESS_PHONES) — they're never metered.
    if from_phone not in FULL_ACCESS_PHONES and not cost_tracker.has_credits(db, student.id):
        await send_whatsapp_message(
            from_phone, "You're out of AI credits — ask your school to top up your account to keep chatting with me!"
        )
        return

    if parsed:
        _, message_text = parsed
    else:
        audio_parsed = parse_incoming_audio(payload)
        image_parsed = parse_incoming_image(payload) if not audio_parsed else None
        document_parsed = parse_incoming_document(payload) if not (audio_parsed or image_parsed) else None

        if audio_parsed:
            _, media_url = audio_parsed
            if not student.has_feature("voice"):
                await send_whatsapp_message(
                    from_phone, "Voice messages aren't available on your account yet — please type your question instead."
                )
                return

            audio_bytes = await download_media(media_url)
            if audio_bytes is None:
                await send_whatsapp_message(from_phone, "Sorry, I couldn't download that voice note — please try sending it again.")
                return

            message_text = await transcribe_audio(audio_bytes)
            if not message_text:
                await send_whatsapp_message(from_phone, "Sorry, I couldn't understand that voice note — could you try again or type your question?")
                return
            cost_tracker.record_minute_usage(db, "sarvam_stt", get_duration_seconds(audio_bytes) / 60, student.id)

            if student.gender is None:
                # Only estimate once — a cheap local computation (no API
                # cost), used to pick an opposite-gender voice for future
                # replies. Never re-guessed once set.
                detected_gender = detect_gender_from_pitch(audio_bytes)
                if detected_gender:
                    student.gender = detected_gender
                    db.commit()
        elif image_parsed:
            _, media_url = image_parsed
            if not student.has_feature("ocr"):
                await send_whatsapp_message(
                    from_phone, "Photo questions aren't available on your account yet — please type your question instead."
                )
                return

            image_bytes = await download_media(media_url)
            if image_bytes is None:
                await send_whatsapp_message(from_phone, "Sorry, I couldn't download that photo — please try sending it again.")
                return

            message_text = await extract_text_from_image(image_bytes)
            if not message_text:
                await send_whatsapp_message(from_phone, "Sorry, I couldn't read any text in that photo — could you try a clearer picture or type your question?")
                return
            cost_tracker.record_flat_usage(db, "azure_ocr", student.id)
        elif document_parsed:
            _, media_url = document_parsed
            if not student.has_feature("documents"):
                await send_whatsapp_message(
                    from_phone,
                    "PDF/Word file questions aren't available on your account yet — please type your question instead.",
                )
                return

            document_bytes = await download_media(media_url)
            if document_bytes is None:
                await send_whatsapp_message(from_phone, "Sorry, I couldn't download that file — please try sending it again.")
                return

            filename = guess_filename_from_media_url(media_url)
            message_text = extract_text_from_document(document_bytes, filename)
            if not message_text:
                await send_whatsapp_message(from_phone, "Sorry, I couldn't read any text in that file — could you try a different file or type your question?")
                return
        else:
            # Unsupported message type (sticker, location, contact card,
            # button/list reply, etc.) — let the student know rather than
            # going silent.
            await send_whatsapp_message(
                from_phone, "Sorry, I can only handle text, voice notes, photos, and documents right now."
            )
            return

    if not message_text:
        return

    # Computed BEFORE this message is saved, so "days since last message"
    # reflects the prior session, not this one — otherwise it would always
    # read as 0 days once this message is in chat_history.
    welcome_back_note = get_welcome_back_note(db, student.id)

    if len(message_text) >= ACTIVE_DOCUMENT_MIN_CHARS:
        student.active_document_text = message_text[:ACTIVE_DOCUMENT_MAX_CHARS]
        db.commit()

    # Pull recent conversation turns *before* saving this message, so the
    # tutor has context but doesn't see this message twice.
    prior_rows = (
        db.query(ChatHistory)
        .filter(ChatHistory.student_id == student.id)
        .order_by(ChatHistory.created_at.desc())
        .limit(HISTORY_TURNS)
        .all()
    )
    history = [{"role": row.role, "content": row.message} for row in reversed(prior_rows)]

    # Save the incoming message
    db.add(ChatHistory(student_id=student.id, role="user", message=message_text, agent="tutor"))
    db.commit()

    image_prompt = None
    video_query = None
    did_answer_via_llm = False
    monthly_spend_before = _monthly_spend_status(db, student)
    quiz_topic_request = extract_quiz_topic(message_text)

    if student.pending_profile_field == "class_confirm" and looks_like_confirmation_reply(message_text):
        # Special case, not a normal profile field — confirming (or declining)
        # the class-update suggestion triggered by a run of off-level questions.
        if _looks_affirmative(message_text):
            student.class_ = student.suggested_class
            reply_text = f"Got it, updated your class to {student.suggested_class}! 👍 What else can I help you with?"
        else:
            reply_text = "No worries, I'll leave it as is! What else can I help you with?"
        student.pending_profile_field = None
        student.suggested_class = None
        db.commit()
        detected_lang = student.preferred_language or "en-IN"
    elif student.pending_profile_field and looks_like_answer(student.pending_profile_field, message_text):
        # This message is the answer to a profile question we asked last turn,
        # not a new tutoring question — no LLM call, so just keep whatever
        # language this student was already using.
        setattr(student, student.pending_profile_field, clean_answer(student.pending_profile_field, message_text))
        student.pending_profile_field = None
        db.commit()
        reply_text = "Got it, thanks! 👍 What else can I help you with?"
        detected_lang = student.preferred_language or "en-IN"
    elif student.active_quiz_id and looks_like_quiz_stop(message_text):
        # Free — no LLM call, so available even if the monthly credit is
        # exhausted (a student shouldn't get stuck unable to exit a quiz).
        student.active_quiz_id = None
        db.commit()
        reply_text = "No problem, quiz stopped! What else can I help you with?"
        detected_lang = student.preferred_language or "en-IN"
    elif looks_like_progress_request(message_text):
        # Computed directly from real TopicProgress rows — no LLM call, so
        # no cost and no risk of the model inventing stats. Checked before
        # the monthly credit-limit gate since it costs nothing either way.
        stats = get_student_stats(db, student.id)
        activity = get_activity_stats(db, student.id)
        coverage = get_chapter_coverage(db, student)
        reply_text = format_progress_message(stats, activity, coverage)
        detected_lang = student.preferred_language or "en-IN"
    elif looks_like_referral_request(message_text):
        # Free — no LLM call. Generated lazily here too (not just at
        # signup) so students created before this feature shipped still get
        # a code the first time they ask.
        if not student.referral_code:
            student.referral_code = generate_referral_code(student.id)
            db.commit()
        reply_text = (
            f"Share your code with a friend — when they message me for the first time and start "
            f"asking questions, you'll get ₹{cost_tracker.REFERRAL_BONUS:.0f} in AI credits "
            f"(up to ₹{cost_tracker.REFERRAL_LIFETIME_CAP:.0f} total)! 🎉\n\n"
            f"Your code: *{student.referral_code}*\n"
            f"Tell them to just message me and mention this code in their first message."
        )
        detected_lang = student.preferred_language or "en-IN"
    elif monthly_spend_before >= MONTHLY_STUDENT_CREDIT_LIMIT:
        # Monthly ₹ credit limit reached — hard stop before the paid LLM
        # call, since this is the core metered feature the subscription
        # price is actually sized against.
        #
        # Sent directly and returned early, deliberately NOT saved to
        # chat_history — a system notice like this isn't tutoring content,
        # and saving it as an "assistant" turn pollutes the model's own
        # conversation history: confirmed live, a later real reply echoed
        # this exact message back verbatim because it was sitting in
        # history as if it were the tutor's own prior statement.
        translated_limit_msg = None
        lang = student.preferred_language or "en-IN"
        limit_msg = _monthly_limit_reached_message()
        if lang and not lang.startswith("en"):
            translated_result = await translate_with_claude(limit_msg, lang, speaker_gender=_assistant_voice_gender(student))
            if translated_result:
                translated_limit_msg = translated_result.text
                cost_tracker.record_claude_usage(
                    db, translated_result.model, translated_result.input_tokens, translated_result.output_tokens, student.id,
                    cache_write_tokens=translated_result.cache_write_tokens, cache_read_tokens=translated_result.cache_read_tokens,
                )
        await send_whatsapp_message(from_phone, translated_limit_msg or limit_msg)
        return
    elif student.active_quiz_id:
        # Treat this message as the answer to the current quiz question.
        quiz_questions = db.query(Question).filter(Question.quiz_id == student.active_quiz_id).order_by(Question.id).all()
        answered_count = (
            db.query(Answer).filter(Answer.question_id.in_([q.id for q in quiz_questions])).count()
            if quiz_questions else 0
        )
        if not quiz_questions or answered_count >= len(quiz_questions):
            # Shouldn't normally happen (quiz should already have ended), but guard anyway.
            student.active_quiz_id = None
            db.commit()
            reply_text = "Looks like that quiz already wrapped up! What else can I help you with?"
            detected_lang = student.preferred_language or "en-IN"
        else:
            current_question = quiz_questions[answered_count]
            if looks_like_quiz_skip(message_text):
                # No grading call needed — is_correct=None marks it as
                # skipped rather than wrong, so it doesn't count against
                # the student's score at the end.
                is_correct = None
                feedback = f"Skipped ⏭️ — the answer was *{current_question.correct_answer}*."
            else:
                is_correct, grade_result = await grade_answer(
                    current_question.question_text, current_question.correct_answer, message_text
                )
                cost_tracker.record_claude_usage(
                    db, grade_result.model, grade_result.input_tokens, grade_result.output_tokens, student.id,
                    cache_write_tokens=grade_result.cache_write_tokens, cache_read_tokens=grade_result.cache_read_tokens,
                )
                feedback = "Correct! ✅" if is_correct else f"Not quite — the answer was *{current_question.correct_answer}*."
            db.add(Answer(
                question_id=current_question.id, student_id=student.id,
                given_answer=message_text, is_correct=is_correct,
            ))
            db.commit()
            answered_count += 1
            if answered_count < len(quiz_questions):
                next_question = quiz_questions[answered_count]
                reply_text = f"{feedback}\n\nQuestion {answered_count + 1}/{len(quiz_questions)}: {next_question.question_text}"
            else:
                all_answers = db.query(Answer).filter(Answer.question_id.in_([q.id for q in quiz_questions])).all()
                score = sum(1 for a in all_answers if a.is_correct is True)
                skipped = sum(1 for a in all_answers if a.is_correct is None)
                attempted = len(quiz_questions) - skipped
                student.active_quiz_id = None
                db.commit()
                skip_note = f" ({skipped} skipped)" if skipped else ""
                reply_text = f"{feedback}\n\n🎉 Quiz complete! You scored *{score}/{attempted}*{skip_note}."
        detected_lang = student.preferred_language or "en-IN"
    elif quiz_topic_request:
        questions_data, gen_result = await generate_quiz_questions(quiz_topic_request, student.class_)
        cost_tracker.record_claude_usage(
            db, gen_result.model, gen_result.input_tokens, gen_result.output_tokens, student.id,
            cache_write_tokens=gen_result.cache_write_tokens, cache_read_tokens=gen_result.cache_read_tokens,
        )
        if not questions_data:
            reply_text = (
                "Sorry, I couldn't put together a quiz on that right now — want to try a different "
                "topic, or just ask me questions directly?"
            )
        else:
            quiz = Quiz(student_id=student.id)
            db.add(quiz)
            db.commit()
            db.refresh(quiz)
            for q in questions_data:
                db.add(Question(
                    quiz_id=quiz.id, question_type="short_answer",
                    question_text=q["question"], correct_answer=q["answer"],
                ))
            db.commit()
            student.active_quiz_id = quiz.id
            db.commit()
            reply_text = (
                f"Great, let's do a {len(questions_data)}-question quiz on *{quiz_topic_request}*! 📝\n\n"
                f"Question 1/{len(questions_data)}: {questions_data[0]['question']}"
            )
        detected_lang = student.preferred_language or "en-IN"
    else:
        did_answer_via_llm = True

        # This is a real tutoring question (not onboarding/quiz/profile
        # noise) — the activity signal the day1-3/week2/week3 referral
        # milestones are checked against (see evaluate_referral_milestones).
        # No-ops immediately for non-referred students or once every
        # milestone's already settled.
        evaluate_referral_milestones(db, student)
        evaluate_habit_milestones(db, student)

        # Either there was no pending question, or the student ignored it and
        # asked something else — drop the pending question either way so we
        # don't keep misreading their answers, then answer normally.
        if student.pending_profile_field:
            student.pending_profile_field = None
            student.suggested_class = None
            db.commit()

        # Surface topics this student has previously gotten wrong, so the
        # tutor can naturally revisit them rather than forgetting the moment
        # they scroll out of the ~6-exchange conversation window.
        weak_topic_rows = (
            db.query(TopicProgress.topic)
            .filter(TopicProgress.student_id == student.id, TopicProgress.is_correct.is_(False))
            .order_by(TopicProgress.created_at.desc())
            .limit(WEAK_TOPICS_LIMIT)
            .all()
        )
        weak_topics = list(dict.fromkeys(row[0] for row in weak_topic_rows))  # dedupe, keep order

        # Route to the Tutor Agent -> real LLM call, with conversation history.
        # The reply language is decided by a separate deterministic classify()
        # call inside tutor_agent.respond(), not by the main generation — see
        # the "lang" field it returns. It also decides whether a diagram is
        # warranted (only if image generation is enabled for this student),
        # returning an image_prompt when so, and whether a text message
        # should selectively get a voice reply (only if explicitly asked
        # for — most text stays text).
        result = await tutor_agent.respond(
            student.as_profile_dict(),
            message_text,
            history,
            weak_topics,
            image_generation_enabled=student.has_feature("image_generation"),
            voice_enabled=student.has_feature("voice"),
            video_enabled=student.has_feature("youtube_videos"),
            active_document_text=student.active_document_text,
        )
        reply_text = result["reply"]
        detected_lang = result["lang"]
        image_prompt = result["image_prompt"]
        video_query = result["video_query"]
        usage = result["usage"]
        cost_tracker.record_claude_usage(
            db, usage["main_model"], usage["main_input_tokens"], usage["main_output_tokens"], student.id,
            cache_write_tokens=usage["main_cache_write_tokens"], cache_read_tokens=usage["main_cache_read_tokens"],
        )
        cost_tracker.record_claude_usage(
            db, usage["classify_model"], usage["classify_input_tokens"], usage["classify_output_tokens"], student.id,
            cache_write_tokens=usage["classify_cache_write_tokens"], cache_read_tokens=usage["classify_cache_read_tokens"],
        )
        if result["wants_audio_reply"]:
            send_voice_reply = True

        # Soft weekly caps: voice/image/video are supplements, not the core
        # paid feature, so hitting their cap just skips that extra (falling
        # back to the text reply, which already stands on its own) rather
        # than blocking the whole turn like the monthly ₹ credit hard stop.
        # One query for all three instead of up to three separate ones.
        weekly_counts = _weekly_usage_snapshot(db, student)
        if send_voice_reply:
            used, limit, _ = _usage_status(student, "voice", weekly_counts)
            if used >= limit:
                send_voice_reply = False
        if image_prompt:
            used, limit, _ = _usage_status(student, "image_generation", weekly_counts)
            if used >= limit:
                image_prompt = None
        if video_query:
            used, limit, _ = _usage_status(student, "youtube_videos", weekly_counts)
            if used >= limit:
                video_query = None

        if detected_lang != student.preferred_language:
            student.preferred_language = detected_lang
            db.commit()

        # Academic-integrity signal for the teacher digest — how often the
        # tutor gave a hint vs. a full worked solution when the student
        # brought a problem to solve. None means "not applicable this turn"
        # (an explanation, check question, etc., not a problem to solve).
        if result["solved_directly"] is True:
            student.direct_solutions_count = (student.direct_solutions_count or 0) + 1
            db.commit()
        elif result["solved_directly"] is False:
            student.hints_given_count = (student.hints_given_count or 0) + 1
            db.commit()

        if result["evaluated"]:
            # The check question being evaluated was asked in the *previous*
            # assistant turn, not this one.
            last_assistant_turn = next((h["content"] for h in reversed(history) if h["role"] == "assistant"), None)
            db.add(
                TopicProgress(
                    student_id=student.id,
                    topic=result["topic"] or "unknown",
                    question_text=last_assistant_turn,
                    given_answer=message_text,
                    is_correct=result["correct"],
                )
            )
            db.commit()

        # Track a run of consecutive off-level questions (e.g. registered as
        # class 8 but repeatedly asking Class 12 content) and, after a real
        # pattern rather than a single one-off question, suggest updating
        # their registered class — never on the first occurrence.
        if result["off_level_class"] and result["off_level_class"] != student.class_:
            student.off_level_count = (student.off_level_count or 0) + 1
        else:
            student.off_level_count = 0
        db.commit()

        if (
            result["off_level_class"]
            and student.off_level_count >= OFF_LEVEL_SUGGEST_THRESHOLD
            and not student.pending_profile_field
            and not reply_text.rstrip().endswith("?")
        ):
            level = result["off_level_class"]
            reply_text = (
                f"{reply_text}\n\nBy the way, I've noticed you've been asking Class {level} questions — "
                f"want me to update your registered class to {level}?"
            )
            student.pending_profile_field = "class_confirm"
            student.suggested_class = level
            student.off_level_count = 0
            db.commit()

        user_message_count = (
            db.query(ChatHistory)
            .filter(ChatHistory.student_id == student.id, ChatHistory.role == "user")
            .count()
        )
        # Don't stack a profile question onto a reply that already ends with
        # the tutor's own question (e.g. a check-for-understanding question,
        # or the class-update suggestion above) — a student answering both at
        # once ("Radiant and centripetal") gets entirely swallowed as the
        # profile answer, silently dropping the actual teaching evaluation.
        if should_ask_this_turn(user_message_count) and not reply_text.rstrip().endswith("?"):
            missing = next_missing_field(student)
            if missing:
                field, question = missing
                reply_text = f"{reply_text}\n\n{question}"
                student.pending_profile_field = field
                db.commit()

    if welcome_back_note:
        reply_text = f"{welcome_back_note}\n\n{reply_text}"

    # Save the reply — kept in English (what Claude actually said) so future
    # turns give the model a consistent conversation history to reason over.
    # Deliberately BEFORE the usage-threshold notice below: that notice is a
    # system aside, not tutoring content, and saving it into history would
    # let a future turn see it as if it were the tutor's own prior statement
    # and potentially echo it back (confirmed live during testing).
    db.add(ChatHistory(student_id=student.id, role="assistant", message=reply_text, agent="tutor"))
    db.commit()

    # This turn's own AI usage just added to this month's spend — warn at
    # 50%/90%/100% of the ₹100 monthly credit so the hard stop is never a
    # surprise. Only added to the outgoing message, never to what was just
    # saved to chat_history above. Only relevant when a real LLM call
    # actually happened this turn (not the profile-answer/class-confirm
    # branches, which never touch the monthly credit).
    if did_answer_via_llm:
        monthly_spend_after = _monthly_spend_status(db, student)
        monthly_notice = _monthly_threshold_notice(monthly_spend_before, monthly_spend_after)
        if monthly_notice:
            reply_text = f"{reply_text}\n\n{monthly_notice}"

    # Translate into the student's language for what actually gets sent.
    # Claude (Haiku) does this first — cheap token cost instead of Sarvam
    # Mayura's per-character billing, which was the single largest cost line
    # item in production. Falls back to Sarvam only if the Claude call fails,
    # and to the plain English reply if not needed (student wrote in
    # English) or both translation paths fail.
    outgoing_text = reply_text
    if detected_lang and not detected_lang.startswith("en"):
        translated_result = await translate_with_claude(reply_text, detected_lang, speaker_gender=_assistant_voice_gender(student))
        if translated_result:
            outgoing_text = translated_result.text
            cost_tracker.record_claude_usage(
                db, translated_result.model, translated_result.input_tokens, translated_result.output_tokens, student.id,
                cache_write_tokens=translated_result.cache_write_tokens, cache_read_tokens=translated_result.cache_read_tokens,
            )
        else:
            translated = await translate_text(reply_text, "en-IN", detected_lang)
            if translated:
                outgoing_text = translated
                cost_tracker.record_char_usage(db, "sarvam_translate", len(reply_text), student.id)

    # Send back over Wati — as a voice note only if the student explicitly
    # asked for an audio reply (never just because their own input was
    # voice), a generated diagram (with the explanation as its caption) if
    # the tutor decided one was warranted, or plain text otherwise. Falls
    # through to text if any richer attempt fails (checking actual success,
    # not just "got a response back" — a failed send still returns a
    # {"sent": False, ...} dict, not None, so checking for None alone would
    # wrongly treat that as handled and never fall back), so the student
    # always gets *something*.
    send_result = None
    if send_voice_reply:
        speaker = OPPOSITE_GENDER_SPEAKER.get(student.gender)
        audio_reply = await synthesize_speech(outgoing_text, detected_lang, speaker=speaker)
        if audio_reply:
            cost_tracker.record_char_usage(db, "sarvam_tts", len(outgoing_text), student.id)
            send_result = await send_whatsapp_audio(from_phone, audio_reply)
            if not send_result.get("sent"):
                logger.error("send_whatsapp_audio failed for %s: %s", from_phone, send_result)
            else:
                # Also send the text transcript, in the same language as the
                # voice note, so the student has it in writing too.
                await send_whatsapp_message(from_phone, outgoing_text)
    if image_prompt:
        image_bytes = await generate_image(image_prompt)
        if image_bytes:
            cost_tracker.record_flat_usage(db, "azure_image", student.id)
            image_result = await send_whatsapp_image(from_phone, image_bytes, caption=outgoing_text)
            if not image_result.get("sent"):
                logger.error("send_whatsapp_image failed for %s: %s", from_phone, image_result)
            elif not send_result:
                # Voice reply (if any) already covered the spoken explanation;
                # the image send itself counts as having delivered something,
                # so only treat this as the "send_result" when there wasn't
                # already a successful voice send above.
                send_result = image_result
    if not send_result or not send_result.get("sent"):
        fallback_result = await send_whatsapp_message(from_phone, outgoing_text)
        if not fallback_result.get("sent"):
            logger.error("send_whatsapp_message fallback also failed for %s: %s", from_phone, fallback_result)

    if video_query:
        video = await find_best_video(video_query, student_language_code=detected_lang, student_class=student.class_)
        cost_tracker.record_youtube_search(db, student.id)
        if video:
            # Sent as its own follow-up message (Wati has no native rich video
            # embed) rather than folded into the main reply, so the link
            # doesn't get lost inside a long translated paragraph.
            video_result = await send_whatsapp_message(from_phone, f"📺 {video['title']}\n{video['url']}")
            if not video_result.get("sent"):
                logger.error("send video suggestion failed for %s: %s", from_phone, video_result)
