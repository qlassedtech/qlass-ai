import asyncio
import logging
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.core import Student, ChatHistory, TopicProgress, ProcessedWebhookMessage
from app.services.whatsapp_client import (
    parse_incoming_message,
    parse_incoming_audio,
    parse_incoming_image,
    parse_incoming_document,
    parse_incoming_button_reply,
    guess_filename_from_media_url,
    download_media,
    send_whatsapp_message,
    send_whatsapp_audio,
    send_whatsapp_image,
    send_whatsapp_buttons,
    verify_webhook_auth,
)
from app.services.profile_builder import (
    next_missing_field,
    should_ask_this_turn,
)
from app.services.sarvam_client import transcribe_audio, synthesize_speech, translate_text
from app.services.llm_client import translate_with_claude
from app.services.audio_qa import get_duration_seconds, detect_gender_from_pitch
from app.services.ocr_client import extract_text_from_image
from app.services.image_client import generate_image
from app.services.document_client import apply_active_document_pin, extract_text_from_document
from app.services.youtube_client import find_best_video
from app.services import cost_tracker, school_billing
from app.services.escalation import (
    record_hint_outcome,
    get_escalation_recipients,
    format_escalation_message,
    format_student_requested_help_message,
)
from app.services.habit import evaluate_habit_milestones
from app.services.rate_limit import is_rate_limited, student_lock
from app.services import tenancy
from app.services.tenancy import get_qlass_direct_centre_id
from app.services.referral import (
    generate_referral_code,
    extract_referral_code,
    evaluate_referral_milestones,
    is_worth_asking_to_refer,
    REFERRAL_SIGNUP_BONUS,
)
from app.services.intent_classifier import classify_intent
from app.services.retrieval import fetch_candidate_chunks
from app.services.active_profile import (
    classify_profile_routing,
    ProfileRouting,
    get_active_profile,
    set_active_profile,
    build_disambiguation_prompt,
)
from app.services.progress_report import (
    get_student_stats,
    get_activity_stats,
    get_welcome_back_note,
    get_chapter_coverage,
    format_progress_message,
)
from app.services.quiz_flow import handle_quiz_answer, start_mock_test, start_quiz, stop_quiz
from app.agents.tutor_agent import TutorAgent

logger = logging.getLogger(__name__)

router = APIRouter()
tutor_agent = TutorAgent()

HISTORY_TURNS = 12  # ~6 back-and-forth exchanges of prior context
WEBHOOK_LEASE_SECONDS = 5 * 60
WEBHOOK_RETRY_INTERVAL_SECONDS = 30
WEBHOOK_RETRY_BATCH_SIZE = 100
WEAK_TOPICS_LIMIT = 5
OFF_LEVEL_SUGGEST_THRESHOLD = 3  # consecutive off-level questions before suggesting a class update

# Interactive quick-menu (see send_whatsapp_buttons/parse_incoming_button_reply
# in whatsapp_client.py) — each button maps to the same canonical phrase its
# corresponding typed command already matches, so a button tap is routed
# through the exact same intent-classification path as if the student had
# typed it (see classify_intent below) rather than needing its own path.
#
# Deliberately no "Talk to Teacher" button here — proactively offering a
# human escalation option on the AI tutor's own menu undercuts its own
# credibility (it should be the front line, not routinely pointing to a
# human). That path stays reachable two ways: the student explicitly types
# it (still handled below via the teacher_help intent, regardless of any
# button), or the automatic hint-streak escalation offers it after the
# student has genuinely struggled (see app.services.escalation).
#
# "Refer a Friend" is also NOT always shown — see MENU_BUTTONS_BASE below,
# it's added dynamically once the student has actually engaged enough to
# credibly vouch for the product (asking a brand-new student to refer a
# friend before they've experienced any value reads as growth-hungry, not
# confident in the teaching itself).
MENU_BUTTONS_BASE = ["📊 My Progress"]
MENU_BUTTON_TO_COMMAND = {
    "📊 My Progress": "my progress",
    "🎁 Refer a Friend": "refer a friend",
}

# Offered instead of a plain "you're out of credits" text (see the credit
# gate in _handle_message) — a real option to act on beats a dead end.
# "🏫 Ask My School" maps onto the SAME "talk to teacher" command as the
# main menu button above (reuses get_escalation_recipients — no separate
# path needed), rather than inventing a new intent for what's really the
# same underlying action.
CREDIT_EXHAUSTED_BUTTONS = ["💳 Top Up Credits", "🏫 Ask My School"]
CREDIT_BUTTON_TO_COMMAND = {
    "💳 Top Up Credits": "top up credits",
    "🏫 Ask My School": "talk to teacher",
}

# Never keep production-cost bypasses for particular phone numbers. Feature
# access is provisioned per learner (including by launch_pilot), and every
# account remains subject to the same spend controls.
FULL_ACCESS_PHONES: frozenset[str] = frozenset()

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


async def _send_localized_notice(db: Session, from_phone: str, student: Student, message: str) -> None:
    """
    Send a system notice (churn/pilot/credit-exhausted/limit-reached) in the
    student's own detected language, same as every other outgoing reply —
    without this, a Hindi-speaking student would suddenly get one message in
    plain English right when something's already gone wrong for them, which
    is exactly the wrong moment to be harder to understand.
    """
    lang = student.preferred_language or "en-IN"
    if lang and not lang.startswith("en"):
        translated_result = await translate_with_claude(
            message, lang, speaker_gender=_assistant_voice_gender(student), student_state=student.state
        )
        if translated_result:
            message = translated_result.text
            cost_tracker.record_claude_usage(
                db, translated_result.model, translated_result.input_tokens, translated_result.output_tokens, student.id,
                cache_write_tokens=translated_result.cache_write_tokens, cache_read_tokens=translated_result.cache_read_tokens,
            )
    await send_whatsapp_message(from_phone, message)


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


async def _create_new_student(db: Session, from_phone: str, message_text: str = "") -> Student:
    # All features unlocked during the trial (spent down from TRIAL_CREDITS)
    # rather than the conservative all-off default used for school-
    # provisioned students — a prospective family evaluating Qlass for the
    # first time should see the real, full product, not a stripped-down
    # demo that undersells it and risks losing the sale before it starts.
    features = {"voice": True, "ocr": True, "image_generation": True, "documents": True, "youtube_videos": True}

    referred_by_id = None
    referral_code = extract_referral_code(message_text)
    if referral_code:
        referrer = db.query(Student).filter(Student.referral_code == referral_code).first()
        if referrer:
            referred_by_id = referrer.id

    centre_id = get_qlass_direct_centre_id(db)
    student = Student(
        name="New Student", phone=from_phone, features=features,
        centre_id=centre_id, referred_by_id=referred_by_id,
        board=tenancy.default_board_for_centre(db, centre_id),
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
        # Same silent-credit gap as the day1/2/3/week2/3 milestones (see
        # evaluate_referral_milestones) — a bonus the referrer never hears
        # about isn't a good referral experience. Best-effort: shouldn't
        # block the new student's own signup if this send fails.
        referrer = db.query(Student).filter(Student.id == referred_by_id).first()
        if referrer is not None:
            try:
                await send_whatsapp_message(
                    referrer.phone,
                    f"🎉 Your friend just joined using your referral code! You've earned "
                    f"₹{REFERRAL_SIGNUP_BONUS:.0f} in bonus AI credits.",
                )
            except Exception:
                pass
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
        return await _create_new_student(db, from_phone, message_text), None

    # Skipped entirely for audio/image/document messages (empty probe text
    # at this point — see _handle_message) rather than spending a call on
    # nothing to classify; same accepted gap as before AI replaced the
    # regex here (a student on an ambiguous shared phone still gets asked
    # "who's this?" before a voice/photo message is processed).
    routing = (
        await classify_profile_routing(message_text, [s.name for s in students if s.name])
        if message_text.strip()
        else ProfileRouting(False, None, None, None)
    )

    def resolved(student: Student, early_reply: str | None = None) -> tuple[Student, str | None]:
        # Billed to whichever student this classification ultimately
        # resolved to (or the default students[0] on a still-ambiguous
        # result) — this is a real LLM call now, unlike the regex it
        # replaced, so it must be recorded like any other.
        if routing.llm_result is not None:
            cost_tracker.record_claude_usage(
                db, routing.llm_result.model, routing.llm_result.input_tokens, routing.llm_result.output_tokens,
                student.id, cache_write_tokens=routing.llm_result.cache_write_tokens,
                cache_read_tokens=routing.llm_result.cache_read_tokens,
            )
        return student, early_reply

    if routing.new_profile_request:
        new_student = await _create_new_student(db, from_phone)
        await set_active_profile(from_phone, new_student.id)
        missing = next_missing_field(new_student)
        question = missing[1] if missing else "What's your name?"
        if missing:
            new_student.pending_profile_field = missing[0]
            db.commit()
        return resolved(new_student, f"Got it, setting up a new profile! {question}")

    if len(students) == 1:
        return resolved(students[0])

    # Explicit switch request ("switch to Raj", "it's Priya now") overrides
    # any cached active profile — without this, once one sibling's session
    # is cached, the other has no way to take over except waiting out the
    # TTL or happening to mention their own name in an ordinary question.
    if routing.switch_target:
        match = next((s for s in students if s.name and s.name.lower() == routing.switch_target.lower()), None)
        if match:
            await set_active_profile(from_phone, match.id)
            return resolved(match, f"Switched! Hey {match.name}, what can I help you with? 😊")
        # Named someone we don't have a profile for — don't silently create
        # one on a guess (typo risk); ask instead.
        existing_names = ", ".join(s.name for s in students if s.name)
        return resolved(students[0], (
            f"I don't have a profile for '{routing.switch_target}' yet — I've got {existing_names} on this number. "
            f"Want me to set up a new profile instead?"
        ))

    active_id = await get_active_profile(from_phone)
    if active_id:
        match = next((s for s in students if s.id == active_id), None)
        if match:
            return resolved(match)

    if routing.name_match:
        match = next((s for s in students if s.name == routing.name_match), None)
        if match:
            await set_active_profile(from_phone, match.id)
            return resolved(match)

    prompt = build_disambiguation_prompt([s.name for s in students])
    return resolved(students[0], prompt)


@router.post("/webhook")
async def receive_message(request: Request):
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

    # Persist the work before acknowledging Wati. The old implementation
    # marked a message as processed before its background task ran, so a
    # process crash made Wati retries a no-op and permanently lost a student
    # message. The persisted payload is claimed with a lease below and is
    # retried on startup/periodically after a failure or worker crash.
    webhook_message_id = payload.get("whatsappMessageId") or payload.get("id")
    if not webhook_message_id:
        raise HTTPException(status_code=400, detail="Webhook message id is required")

    db = SessionLocal()
    try:
        db.add(ProcessedWebhookMessage(message_id=webhook_message_id, payload=payload, status="pending"))
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(ProcessedWebhookMessage).filter(
            ProcessedWebhookMessage.message_id == webhook_message_id
        ).first()
        if existing and existing.status == "completed":
            return {"received": True, "handled": False, "reason": "duplicate webhook delivery, already processed"}
    finally:
        db.close()

    # Return the Wati acknowledgement independently of tutor processing.
    # The job is already persisted above and the retry worker recovers it if
    # this process exits after the acknowledgement.
    asyncio.create_task(_process_queued_webhook(webhook_message_id))
    return {"received": True, "handled": "processing"}


def _claim_webhook_job(db: Session, message_id: str, now: datetime) -> bool:
    """
    Atomically claim one job via a single conditional UPDATE, so two backend
    processes racing on the same message_id can never both "win" — a prior
    read-job-then-write-status version was safe with a single process (no
    `await` between the read and the write, so nothing else in that event
    loop could interleave) but broke the moment more than one backend
    process/replica existed, since each has its own DB connection and
    Python interpreter with no shared in-process ordering.
    """
    lease_until = now + timedelta(seconds=WEBHOOK_LEASE_SECONDS)
    claimed = db.query(ProcessedWebhookMessage).filter(
        ProcessedWebhookMessage.message_id == message_id,
        ProcessedWebhookMessage.status == "pending",
    ).update(
        {
            "status": "processing",
            "attempts": ProcessedWebhookMessage.attempts + 1,
            "lease_expires_at": lease_until,
            "last_error": None,
        },
        synchronize_session=False,
    )
    if claimed == 0:
        # Either already completed, or another worker holds (or recently
        # held) it — only reclaim if its lease has actually expired.
        claimed = db.query(ProcessedWebhookMessage).filter(
            ProcessedWebhookMessage.message_id == message_id,
            ProcessedWebhookMessage.status == "processing",
            ProcessedWebhookMessage.lease_expires_at < now,
        ).update(
            {
                "status": "processing",
                "attempts": ProcessedWebhookMessage.attempts + 1,
                "lease_expires_at": lease_until,
                "last_error": None,
            },
            synchronize_session=False,
        )
    db.commit()
    return claimed > 0


async def _process_queued_webhook(message_id: str) -> None:
    """
    Claim and process one persisted webhook job. The atomic claim means
    concurrent API workers/processes can safely see the same job without
    sending a duplicate reply.
    """
    db = SessionLocal()
    payload = None
    try:
        now = datetime.now(timezone.utc)
        if not _claim_webhook_job(db, message_id, now):
            return

        job = db.query(ProcessedWebhookMessage).filter(ProcessedWebhookMessage.message_id == message_id).first()
        if job is None:
            return
        payload = job.payload
        if payload is None:
            # A row from before the payload column existed (migration 0033)
            # — it was already handled under the old dedupe-only scheme and
            # has nothing left to replay. Close it out instead of retrying
            # forever.
            job.status = "completed"
            job.lease_expires_at = None
            db.commit()
            return

        phone = payload.get("waId")
        async with (student_lock(phone) if phone else nullcontext()):
            await _handle_message(db, payload)

        job = db.query(ProcessedWebhookMessage).filter(ProcessedWebhookMessage.message_id == message_id).first()
        if job:
            job.status = "completed"
            job.lease_expires_at = None
            db.commit()
    except Exception as exc:
        db.rollback()
        job = db.query(ProcessedWebhookMessage).filter(ProcessedWebhookMessage.message_id == message_id).first()
        if job:
            job.status = "pending"
            job.lease_expires_at = None
            job.last_error = str(exc)[:1000]
            db.commit()
        logger.exception("Webhook job %s failed and will be retried", message_id)
    finally:
        db.close()


async def retry_pending_webhooks() -> None:
    """Continuously recover persisted jobs left pending by failed workers."""
    while True:
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            jobs = (
                db.query(ProcessedWebhookMessage.message_id)
                .filter(
                    (ProcessedWebhookMessage.status == "pending")
                    | ((ProcessedWebhookMessage.status == "processing") & (ProcessedWebhookMessage.lease_expires_at < now))
                )
                .order_by(ProcessedWebhookMessage.processed_at)
                .limit(WEBHOOK_RETRY_BATCH_SIZE)
                .all()
            )
        finally:
            db.close()
        for (message_id,) in jobs:
            await _process_queued_webhook(message_id)
        await asyncio.sleep(WEBHOOK_RETRY_INTERVAL_SECONDS)


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
    button_reply = parse_incoming_button_reply(payload)
    if button_reply:
        button_from_phone, button_text = button_reply
        command = MENU_BUTTON_TO_COMMAND.get(button_text) or CREDIT_BUTTON_TO_COMMAND.get(button_text, button_text)
        parsed = (button_from_phone, command)
    else:
        parsed = parse_incoming_message(payload)
    probe_text = parsed[1] if parsed else ""
    student, early_reply = await _resolve_active_student(db, from_phone, probe_text)
    if early_reply:
        await send_whatsapp_message(from_phone, early_reply)
        return

    # Tapping "💳 Top Up Credits" (see the credit-exhausted notice below)
    # must work regardless of churn/pilot/credit state — sending a payment
    # link costs no AI credits, so it's intercepted here, before any of
    # those gates, rather than getting stuck behind the very check it's
    # meant to resolve.
    if probe_text == "top up credits":
        # student_id disambiguates a shared family phone with more than one
        # child on it — without it, a payment could silently land in a
        # sibling's wallet instead of the one actually texting right now.
        pay_link = f"{settings.portal_base_url}/pay?phone={student.phone}&student_id={student.id}"
        await send_whatsapp_message(
            from_phone, f"Here's your top-up link — pay directly and credits land instantly:\n{pay_link}",
        )
        return

    # A school marked "churned" in the sales pipeline (see
    # app.services.sales) is no longer a paying customer — its students
    # only keep getting service if THEY personally paid Qlass directly at
    # some point (a real Razorpay payment, not school-funded trial/
    # referral/habit credit or a manual grant). Checked before the credit
    # check below and before FULL_ACCESS_PHONES, since a churned school's
    # own demo numbers shouldn't get free service either.
    if from_phone not in FULL_ACCESS_PHONES and school_billing.is_centre_churned(db, student.centre_id) \
            and not cost_tracker.has_independent_payment(db, student.id):
        await _send_localized_notice(
            db, from_phone, student,
            "Your school's Qlass account is currently on hold — ask your school to contact Qlass, "
            "or top up your own AI credits directly to keep chatting with me!",
        )
        return

    if school_billing.is_centre_pilot_expired(db, student.centre_id) and not cost_tracker.has_independent_payment(db, student.id):
        await _send_localized_notice(
            db, from_phone, student,
            "Your school's Qlass pilot has ended. Ask your school to continue the programme, "
            "or top up your own AI credits to keep learning!",
        )
        return

    # Each student has their own wallet now (see cost_tracker) — checked
    # here, after resolving which student this actually is, rather than
    # against one shared account-wide balance. Demo/testing numbers get
    # unlimited credits (see FULL_ACCESS_PHONES) — they're never metered.
    if from_phone not in FULL_ACCESS_PHONES and not cost_tracker.has_credits(db, student.id):
        # A dead-end text notice left a student stuck with no way to act —
        # real options (pay directly, or have the school notified) beat
        # just being told to go ask someone in person.
        notice = "You're out of AI credits for now — here are your options:"
        lang = student.preferred_language or "en-IN"
        if lang and not lang.startswith("en"):
            translated_result = await translate_with_claude(
                notice, lang, speaker_gender=_assistant_voice_gender(student), student_state=student.state
            )
            if translated_result:
                notice = translated_result.text
                cost_tracker.record_claude_usage(
                    db, translated_result.model, translated_result.input_tokens, translated_result.output_tokens,
                    student.id, cache_write_tokens=translated_result.cache_write_tokens,
                    cache_read_tokens=translated_result.cache_read_tokens,
                )
        button_result = await send_whatsapp_buttons(from_phone, notice, CREDIT_EXHAUSTED_BUTTONS)
        if not button_result.get("sent"):
            await send_whatsapp_message(
                from_phone,
                "You're out of AI credits — reply 'top up credits' for your payment link, or 'talk to "
                "teacher' to let your school know, to keep chatting with me!",
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

    apply_active_document_pin(db, student, message_text)

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
    incoming_row = ChatHistory(student_id=student.id, role="user", message=message_text, agent="tutor")
    db.add(incoming_row)
    db.commit()
    db.refresh(incoming_row)

    image_prompt = None
    video_query = None
    citation = None
    did_answer_via_llm = False
    monthly_spend_before = _monthly_spend_status(db, student)
    # Logged once per turn, before the routing ladder below decides which
    # branch handles it — the ladder has grown into ~10 branches across
    # several features (quiz, mock test, escalation, menu, profile
    # onboarding...), so this is the fastest way to answer "why did the bot
    # respond that way" from logs alone, without re-reading the whole ladder.
    logger.info(
        "turn start phone=%s student_id=%s pending_field=%s active_quiz_id=%s text=%r",
        from_phone, student.id, student.pending_profile_field, student.active_quiz_id, message_text[:200],
    )
    # Resolves "quiz on the same"/"quiz on this" — passed to the classifier
    # below so it can substitute the real topic itself rather than quizzing
    # on the literal referential phrase. Prefers last_discussed_topic
    # (updated on EVERY real tutoring turn, see the LLM branch below) over
    # TopicProgress, which is only written when a scored check question is
    # evaluated — relying on TopicProgress alone previously resolved to a
    # stale topic from an unrelated earlier session whenever the answer to
    # today's check question and the quiz request arrived in the same
    # message (confirmed live: "demand and supply" resolved to a leftover
    # physics topic from hours earlier).
    last_topic_row = (
        db.query(TopicProgress.topic)
        .filter(TopicProgress.student_id == student.id)
        .order_by(TopicProgress.created_at.desc())
        .first()
    )
    last_topic_context = student.last_discussed_topic or (last_topic_row[0] if last_topic_row else None)

    # A single cheap, deterministic classification call (same pattern as the
    # tutor's own language classifier) replacing what used to be several
    # separate hardcoded phrase-lists — one per intent, plus separate regex
    # for quiz/mock-test/skip detection. Those only ever matched an exact
    # fixed string (e.g. "my progress", "quiz me on X"), so any other
    # phrasing of the same request (confirmed live: "what is my
    # performance") silently fell through to the LLM improvising an answer
    # instead of the real command. Run for every message that reaches this
    # point, but only students who already passed the churn/pilot/credit
    # gates above ever reach here, so this never spends money on a blocked
    # account.
    last_assistant_message = next((h["content"] for h in reversed(history) if h["role"] == "assistant"), None)
    # Candidate chunks (cheap Postgres full-text recall, no LLM cost) are
    # fetched unconditionally here so classify_intent's single call can
    # also judge their relevance (relevant_excerpts) — see
    # app.services.retrieval's module docstring for why that judgment
    # rides along in this call instead of a separate dedicated one.
    candidate_chunks = fetch_candidate_chunks(db, message_text, student.class_, student.board)
    classification = await classify_intent(
        message_text, last_discussed_topic=last_topic_context, last_assistant_message=last_assistant_message,
        candidate_chunks=candidate_chunks,
    )
    cost_tracker.record_claude_usage(
        db, classification.llm_result.model, classification.llm_result.input_tokens,
        classification.llm_result.output_tokens, student.id,
        cache_write_tokens=classification.llm_result.cache_write_tokens,
        cache_read_tokens=classification.llm_result.cache_read_tokens,
    )
    intent = classification.intent
    quiz_topic_request = classification.quiz_topic
    mock_test_request = classification.wants_mock_test

    if intent == "menu" and not student.active_quiz_id:
        # Free UI affordance — returns immediately without touching
        # chat_history/credits beyond the classification call above.
        # Guarded against an active quiz: "help"/"menu" typed mid-quiz
        # should stay inside the quiz's own answer/skip/stop handling below
        # rather than being hijacked into the main menu — a stuck student
        # typing "help" mid-quiz almost certainly means "help with this
        # question," not "show me the main menu."
        if not prior_rows:
            # A student's very first message ever — "My Progress" and
            # "Refer a Friend" are dead ends with zero history behind them
            # (confirmed live: a first-time student tapped "My Progress"
            # and got "you haven't answered any check questions yet").
            # Skip the returning-user menu entirely and make the first
            # touch feel like meeting a tutor, not a support bot: greet by
            # name if we have one, and demonstrate what's possible instead
            # of just listing commands.
            first_name = student.name if student.name and student.name != "New Student" else None
            greeting = f"Hi {first_name}! 👋" if first_name else "Hi! 👋"
            # Only advertise a capability the student actually has — a
            # first "wow" moment that turns out broken (e.g. promising
            # photo help to a student without the OCR feature) is worse
            # than not mentioning it at all.
            capability_hints = []
            if student.has_feature("ocr"):
                capability_hints.append("send a photo of a tricky homework question")
            if student.has_feature("voice"):
                capability_hints.append("send a voice note")
            if student.has_feature("documents"):
                capability_hints.append("share a PDF or Word file of your homework")
            if student.has_feature("youtube_videos"):
                capability_hints.append("ask for a video explanation")
            if not capability_hints:
                capability_sentence = ""
            elif len(capability_hints) == 1:
                capability_sentence = f" You can also {capability_hints[0]}."
            else:
                capability_sentence = f" You can also {', '.join(capability_hints[:-1])}, or {capability_hints[-1]}."
            await send_whatsapp_message(
                from_phone,
                f"{greeting} I'm your AI tutor — ask me to explain any topic or say \"quiz me on <topic>\" "
                f"to test yourself.{capability_sentence} What would you like to learn today?",
            )
            return
        # A returning student saying "Hi" after a gap is the single most
        # common re-entry message — previously only a real tutoring
        # question got the welcome-back note (spliced in much further down
        # this ladder), so this exact greeting silently skipped it.
        menu_greeting = f"{welcome_back_note}\n\nJust ask me anything to start learning, or pick one of these:" \
            if welcome_back_note else "Hi! Just ask me anything to start learning, or pick one of these:"
        activity = get_activity_stats(db, student.id)
        weekly_stats = get_student_stats(db, student.id, days=7)
        menu_buttons = list(MENU_BUTTONS_BASE)
        if is_worth_asking_to_refer(activity, weekly_stats["messages_sent"]):
            menu_buttons.append("🎁 Refer a Friend")
        button_result = await send_whatsapp_buttons(from_phone, menu_greeting, menu_buttons)
        if not button_result.get("sent"):
            fallback_prefix = f"{welcome_back_note}\n\n" if welcome_back_note else ""
            fallback_commands = "'my progress'" + (", 'refer a friend'," if len(menu_buttons) > 1 else "")
            await send_whatsapp_message(
                from_phone,
                f"{fallback_prefix}Just ask me anything to start learning! Or reply with {fallback_commands} "
                "or anything else you want to work on.",
            )
        return

    if student.pending_profile_field == "class_confirm" and (quiz_topic_request or mock_test_request):
        # An explicit new request in the same message (e.g. "No, quiz me on
        # circular motion") should win over resolving the pending class-
        # update nudge via the LLM below — treat it as an implicit decline
        # (leave the class as-is) and let quiz_topic_request/mock_test_
        # request start the quiz further down in this same if/elif ladder.
        student.pending_profile_field = None
        student.suggested_class = None
        db.commit()

    if student.active_quiz_id and intent == "quiz_stop":
        # No extra LLM call beyond the classification above, so available
        # even if the monthly credit is exhausted (a student shouldn't get
        # stuck unable to exit a quiz).
        reply_text = stop_quiz(db, student)
        detected_lang = student.preferred_language or "en-IN"
    elif intent == "progress":
        # Computed directly from real TopicProgress rows — no risk of the
        # model inventing stats. Checked before the monthly credit-limit
        # gate since the classification call above already ran regardless.
        stats = get_student_stats(db, student.id)
        activity = get_activity_stats(db, student.id)
        coverage = get_chapter_coverage(db, student)
        reply_text = format_progress_message(stats, activity, coverage)
        detected_lang = student.preferred_language or "en-IN"
    elif intent == "referral":
        # Generated lazily here too (not just at signup) so students
        # created before this feature shipped still get a code the first
        # time they ask.
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
    elif intent == "teacher_help":
        # Checked before the monthly credit-limit gate below (same
        # reasoning as progress/referral): asking for a human teacher
        # shouldn't be blocked just because AI credits ran out. Distinct
        # from the automatic hint-streak escalation in app.services.
        # escalation — this is the student explicitly asking, not a
        # system-detected struggle pattern.
        for recipient in get_escalation_recipients(db, student.centre_id):
            await send_whatsapp_message(recipient.phone, format_student_requested_help_message(student.name))
        reply_text = "I've let your teacher know you'd like some help! 🙋 They'll reach out soon. What else can I help you with in the meantime?"
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
        await _send_localized_notice(db, from_phone, student, _monthly_limit_reached_message())
        return
    elif student.active_quiz_id:
        # Treat this message as the answer to the current quiz question.
        reply_text = await handle_quiz_answer(db, student, message_text, classification.quiz_skip)
        detected_lang = student.preferred_language or "en-IN"
    elif mock_test_request:
        mock_topic = classification.mock_test_topic or "a mixed review covering everything we've discussed so far"
        reply_text = await start_mock_test(db, student, mock_topic)
        detected_lang = student.preferred_language or "en-IN"
    elif quiz_topic_request:
        reply_text = await start_quiz(db, student, quiz_topic_request)
        detected_lang = student.preferred_language or "en-IN"
    elif (
        db.query(ChatHistory.id)
        .filter(
            ChatHistory.student_id == student.id, ChatHistory.role == "user",
            ChatHistory.id > incoming_row.id,
        )
        .first()
        is not None
    ):
        # A newer message from this same student already arrived while this
        # one was waiting on student_lock (e.g. three rapid-fire texts like
        # "Then what will I do with theory?" / "No use" / "Bye" sent within
        # seconds of each other) — confirmed live: each got its own
        # independent LLM reply, producing a disjointed pile-up of answers
        # that didn't track what the student was actually saying as one
        # thought. Skip generating a reply for this stale message entirely;
        # it's already in chat_history, so the newer message's own LLM call
        # sees it as context and can respond to the whole exchange at once.
        # Never applies to a quiz answer (that's the separate
        # student.active_quiz_id branch above, not this one) — every quiz
        # answer still needs its own grading feedback.
        return
    else:
        did_answer_via_llm = True

        # This is a real tutoring question (not onboarding/quiz/profile
        # noise) — the activity signal the day1-3/week2/week3 referral
        # milestones are checked against (see evaluate_referral_milestones).
        # No-ops immediately for non-referred students or once every
        # milestone's already settled.
        await evaluate_referral_milestones(db, student)
        await evaluate_habit_milestones(db, student)

        # Whether this message actually answers a pending onboarding question
        # (name/class/board/school) or the class-update nudge is decided by
        # the LLM itself below (pending_profile_field/pending_class_confirm
        # -> result["profile_answer"]/result["class_confirm"]), not a local
        # regex heuristic — a plain word-matching check here previously
        # mis-swallowed mixed messages (e.g. "I don't know. Nikhil", or
        # "12.. no" declining a class-update nudge while also attempting a
        # maths answer), discarding real content entirely. So neither is
        # cleared here; both are resolved after the LLM call instead.
        pending_class_confirm = (
            student.suggested_class if student.pending_profile_field == "class_confirm" else None
        )
        pending_profile_field = (
            student.pending_profile_field if student.pending_profile_field != "class_confirm" else None
        )

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
        relevant_chunks = [
            candidate_chunks[i - 1] for i in classification.relevant_excerpts if 1 <= i <= len(candidate_chunks)
        ]
        result = await tutor_agent.respond(
            student.as_profile_dict(),
            message_text,
            history,
            weak_topics,
            image_generation_enabled=student.has_feature("image_generation"),
            voice_enabled=student.has_feature("voice"),
            video_enabled=student.has_feature("youtube_videos"),
            active_document_text=student.active_document_text,
            pending_class_confirm=pending_class_confirm,
            pending_profile_field=pending_profile_field,
            retrieved_chunks=relevant_chunks,
        )
        reply_text = result["reply"]
        detected_lang = result["lang"]
        image_prompt = result["image_prompt"]
        video_query = result["video_query"]
        citation = result["citation"]

        if pending_class_confirm:
            # The LLM already wove an acknowledgement into reply_text above
            # when it read a yes/no signal — this just applies the actual
            # class change and settles the nudge for good, whatever it
            # decided (including "na", so an ignored suggestion is never
            # re-appended to a future reply).
            if result["class_confirm"] is True:
                student.class_ = pending_class_confirm
            student.pending_profile_field = None
            student.suggested_class = None
            db.commit()
        elif pending_profile_field:
            # Same idea: whatever the LLM extracted (or didn't) settles this
            # turn's onboarding question. If it didn't get an answer this
            # turn, should_ask_this_turn/next_missing_field further below
            # will naturally ask again in a few messages — this doesn't
            # need to keep retrying the same turn.
            if result["profile_answer"]:
                setattr(student, pending_profile_field, result["profile_answer"])
            student.pending_profile_field = None
            db.commit()
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
        if result["topic"]:
            # Updated on every real tutoring turn (not gated on "evaluated"
            # like TopicProgress below) — see Student.last_discussed_topic
            # and the "quiz on the same" resolution near the top of this
            # function.
            student.last_discussed_topic = result["topic"]
            db.commit()

        if result["solved_directly"] is True:
            student.direct_solutions_count = (student.direct_solutions_count or 0) + 1
            db.commit()
        elif result["solved_directly"] is False:
            student.hints_given_count = (student.hints_given_count or 0) + 1
            db.commit()

        should_escalate = record_hint_outcome(student, result["evaluated"], result["correct"])
        if should_escalate:
            student.consecutive_unresolved_hints = 0  # reset so we don't re-notify every single turn past threshold
            db.commit()
            escalation_message = format_escalation_message(student.name, result["topic"])
            for recipient in get_escalation_recipients(db, student.centre_id):
                await send_whatsapp_message(recipient.phone, escalation_message)
            # The explicit "talk to teacher" command already tells the
            # student their teacher was notified — this automatic
            # hint-streak trigger previously didn't, so a student stuck on
            # the same topic had no idea help was already on its way.
            # Skipped on a goodbye turn — a farewell shouldn't get this
            # tacked on.
            if not result["closing"]:
                reply_text = f"{reply_text}\n\nBy the way, I've let your teacher know you might want a hand with this — they'll reach out soon. 🙋"

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
            and not result["closing"]
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
        # Also never onto a goodbye (result["closing"]) — confirmed live: a
        # student who said "Bye" got "Bye! Come back anytime! 👋\n\nBy the
        # way, what's your name?" tacked on right after their own sign-off.
        if should_ask_this_turn(user_message_count) and not reply_text.rstrip().endswith("?") and not result["closing"]:
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
    logger.info(
        "turn end phone=%s student_id=%s via_llm=%s active_quiz_id=%s reply=%r",
        from_phone, student.id, did_answer_via_llm, student.active_quiz_id, reply_text[:200],
    )

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
        translated_result = await translate_with_claude(
            reply_text, detected_lang, speaker_gender=_assistant_voice_gender(student), student_state=student.state
        )
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

    # Appended AFTER translation, not before — a chapter title is a proper
    # noun, and running it through the translation pass risked mangling it
    # into awkward Hindi rather than leaving it as-is (see
    # app.services.retrieval.build_citation_footer). `citation` is None
    # unless this turn actually went through the tutor_agent.respond()
    # branch AND grounding fired — every other branch (quiz, progress,
    # menu, etc.) leaves it at its safe default.
    if citation:
        outgoing_text = f"{outgoing_text}\n\n{citation}"

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
