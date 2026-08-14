import asyncio
import logging
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.core import Centre, Student, Teacher, ProcessedWebhookMessage
from app.services.chat_core import process_message
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
from app.services.sarvam_client import transcribe_audio, synthesize_speech
from app.services.llm_client import translate_with_claude
from app.services.audio_qa import get_duration_seconds, detect_gender_from_pitch
from app.services.ocr_client import extract_text_from_image
from app.services.image_client import generate_image
from app.services.document_client import extract_text_from_document
from app.services import audit_log, cost_tracker, nudges, school_billing
from app.services.escalation import (
    QLASS_SUPPORT_PHONE,
    SCHOOL_REVIEW_STAFF_PHONES,
    get_escalation_recipients,
    format_student_requested_help_message,
)
from app.services.profile_builder import next_missing_field
from app.services.rate_limit import is_rate_limited, is_signup_rate_limited, student_lock
from app.services import tenancy
from app.services.tenancy import get_qlass_direct_centre_id
from app.services.referral import (
    generate_referral_code, extract_referral_code, grant_signup_referral_bonus, REFERRAL_SIGNUP_BONUS,
)
from app.services.active_profile import (
    classify_profile_routing,
    ProfileRouting,
    get_active_profile,
    set_active_profile,
    build_disambiguation_prompt,
)

logger = logging.getLogger(__name__)

router = APIRouter()

WEBHOOK_LEASE_SECONDS = 5 * 60
WEBHOOK_RETRY_INTERVAL_SECONDS = 30
WEBHOOK_RETRY_BATCH_SIZE = 100

# Interactive quick-menu (see send_whatsapp_buttons/parse_incoming_button_reply
# in whatsapp_client.py) — each button maps to the same canonical phrase its
# corresponding typed command already matches, so a button tap is routed
# through the exact same intent-classification path as if the student had
# typed it (see app.services.chat_core.process_message, which decides the
# actual menu content and which buttons to offer) rather than needing its
# own path.
#
# Deliberately no "Talk to Teacher" button here — proactively offering a
# human escalation option on the AI tutor's own menu undercuts its own
# credibility (it should be the front line, not routinely pointing to a
# human). That path stays reachable two ways: the student explicitly types
# it (still handled via the teacher_help intent, regardless of any button),
# or the automatic hint-streak escalation offers it after the student has
# genuinely struggled (see app.services.escalation).
MENU_BUTTON_TO_COMMAND = {
    "📊 My Progress": "my progress",
    "💳 Credit Usage": "credit usage",
    "🎁 Refer a Friend": "refer a friend",
}

# Offered instead of a plain "you're out of credits" text (see the credit
# gate in _handle_message) — a real option to act on beats a dead end.
# "🏫 Ask My School" maps onto the SAME "talk to teacher" command as the
# main menu button above (reuses get_escalation_recipients — no separate
# path needed), rather than inventing a new intent for what's really the
# same underlying action. "📞 Call Us" is a third, always-available option:
# "Ask My School" silently notifies nobody for a self-signup student with
# no real school on file (Teacher.centre_id has zero matches for the
# "Qlass Direct" fallback centre — confirmed live), so a direct phone
# number is the one option guaranteed to reach an actual human regardless
# of whether this student belongs to a partner school at all. Defined
# once in app.services.escalation (imported below) so every channel's
# "talk to a human" fallback — WhatsApp, web, Android, teacher's My AI
# Tutor — points at the same real number.
CREDIT_EXHAUSTED_BUTTONS = ["💳 Top Up Credits", "🏫 Ask My School", "📞 Call Us"]
CREDIT_BUTTON_TO_COMMAND = {
    "💳 Top Up Credits": "top up credits",
    "🏫 Ask My School": "talk to teacher",
    "📞 Call Us": "call qlass",
}

# Attached to a 50%/75% trial-credit downgrade nudge (see
# app.services.chat_core._level_downgrade_offer / ChatTurnResult.
# level_offer) — button text maps onto the exact same canonical phrases
# "level N"/"stay on current level" a typed command already matches (see
# CANONICAL_COMMAND_INTENT), so a tap routes through the identical
# deterministic path as if the student had typed it.
LEVEL_OFFER_BUTTON_TO_COMMAND = {
    "⬇️ Try Level 1": "level 1",
    "⬇️ Try Level 2": "level 2",
    "➡️ Stay As Is": "stay on current level",
}


def _level_offer_buttons(offered_level: int) -> list[str]:
    return [f"⬇️ Try Level {offered_level}", "➡️ Stay As Is"]

# For the manual "🎓 Change Level" flow (see ChatTurnResult.level_choice) —
# distinct from LEVEL_OFFER_BUTTON_TO_COMMAND above, which only ever offers
# ONE specific cheaper level plus "stay". Here the student picked "change
# level" themselves with no target, so all levels other than their current
# one are real candidates — always exactly 3 buttons (the one WhatsApp
# interactive-button cap allows), since one of the 4 is always excluded.
LEVEL_CHOICE_BUTTON_LABELS = {1: "🎯 Level 1", 2: "⚡ Level 2", 3: "📘 Level 3", 4: "🧠 Level 4"}
LEVEL_CHOICE_BUTTON_TO_COMMAND = {label: f"level {n}" for n, label in LEVEL_CHOICE_BUTTON_LABELS.items()}


def _level_choice_buttons(current_level: int) -> list[str]:
    return [label for level, label in LEVEL_CHOICE_BUTTON_LABELS.items() if level != current_level]

# Quick Reply buttons on the "school_onboarding_review_alert" WhatsApp
# template (see app.routers.admin.register_school) — approved in Wati by
# Qlass staff, not this codebase, so these labels must match exactly what
# was configured there. Deliberately STATIC (no per-message dynamic
# payload — Wati's template send API doesn't support that for Quick Reply
# buttons), so a tap resolves to whichever self-registered school is most
# recently still awaiting review (see _handle_school_review_button) rather
# than one encoded in the button itself; fine given how infrequently
# schools self-register.
SCHOOL_REVIEW_BUTTON_ACTIONS = {"Approve School": "active", "Reject School": "churned"}


async def _handle_school_review_button(db: Session, from_phone: str, new_status: str) -> None:
    """
    Approve/reject a self-registered school (see app.routers.admin.
    register_school, which sends the alert this button is attached to)
    directly from WhatsApp — no portal login needed for the common case.
    "Reject" reuses sales_status="churned" rather than a new status value:
    school_billing.is_centre_churned already gates real usage for exactly
    that status, so rejecting immediately blocks the school's students
    from free-riding on the trial credit, using logic that's already live
    and tested rather than adding a new lifecycle state.
    """
    centre = (
        db.query(Centre)
        .filter(Centre.sales_status == "prospect")
        .order_by(Centre.created_at.desc())
        .first()
    )
    if centre is None:
        await send_whatsapp_message(from_phone, "No self-registered school is currently awaiting review.")
        return
    # Confirmed live (security review, Aug 2026): this webhook has no
    # signature verification (WATI_WEBHOOK_SECRET unset — a known,
    # accepted gap), so the SCHOOL_REVIEW_STAFF_PHONES check above is a
    # phone-string match on attacker-controlled input, not real auth. For
    # "Approve" the blast radius is limited (a prospect school already has
    # working access — this just relabels the sales pipeline). "Reject"
    # is the dangerous direction: it actively blocks real usage via
    # school_billing.is_centre_churned. Once a school has real activity
    # (more than a handful of real roster rows), a forged "Reject" tap
    # would deny service to people who are actually using the product —
    # that action now requires a real portal login (PATCH /admin/school)
    # instead, which IS properly authenticated. A brand-new, still-empty
    # prospect (the common case this button is actually for) can still be
    # rejected straight from WhatsApp.
    if new_status == "churned":
        real_activity = (
            db.query(Student.id).filter(Student.centre_id == centre.id).limit(6).count()
            + db.query(Teacher.id).filter(Teacher.centre_id == centre.id).limit(6).count()
        )
        if real_activity > 5:
            await send_whatsapp_message(
                from_phone,
                f"\"{centre.name}\" already has real students/teachers enrolled, so it can't be rejected from "
                f"WhatsApp anymore — use the portal (PATCH /admin/school) to change its status instead.",
            )
            return
    centre.sales_status = new_status
    db.commit()
    audit_log.record(
        db, None, f"school_review_{new_status}", "centre", centre.id,
        detail=f"via WhatsApp button, staff phone {from_phone}",
    )
    verb = "Approved ✅" if new_status == "active" else "Rejected ❌"
    await send_whatsapp_message(from_phone, f"{verb}: \"{centre.name}\" ({centre.city or 'no city given'}).")


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


def _extract_school_centre_from_greeting(db: Session, message_text: str) -> Centre | None:
    """
    Matches a school's own name appearing in a first-contact WhatsApp
    message — the school's own click-to-chat link (see SchoolProfile.tsx's
    "WhatsApp Link") pre-fills "Hi, I am a student from <School Name>. I am
    excited to get access to AI tutor", deliberately worded as something a
    real student could plausibly have typed themselves rather than a
    machine-looking code, so a brand-new signup's very first message
    attributes them to the right school without needing any web form at
    all. "Qlass Direct" is excluded — it's Qlass's own internal fallback
    label (see tenancy.QLASS_DIRECT_CENTRE_NAME), never a real school a
    student would actually name.
    """
    text_lower = message_text.lower()
    for centre in db.query(Centre).filter(Centre.name != tenancy.QLASS_DIRECT_CENTRE_NAME).all():
        if centre.name.lower() in text_lower:
            return centre
    return None


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

    school_centre = _extract_school_centre_from_greeting(db, message_text)
    centre_id = school_centre.id if school_centre else get_qlass_direct_centre_id(db)
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
        # which DO require the referred student to actually engage) — capped
        # per referrer per day (see referral.grant_signup_referral_bonus) so
        # that lack of an activity gate isn't directly farmable.
        paid = grant_signup_referral_bonus(db, referred_by_id)
        # Same silent-credit gap as the day1/2/3/week2/3 milestones (see
        # evaluate_referral_milestones) — a bonus the referrer never hears
        # about isn't a good referral experience. Best-effort: shouldn't
        # block the new student's own signup if this send fails.
        referrer = db.query(Student).filter(Student.id == referred_by_id).first()
        if paid and referrer is not None:
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
    For the overwhelmingly common case (one profile per phone) this really
    is a single query with zero extra overhead (see the len(students) == 1
    check below for why that wasn't actually true until it was fixed); the
    multi-profile path only kicks in for phones that actually have more
    than one student on them.

    Matches against `phone` OR `whatsapp_phone` — the latter covers a
    student whose primary/login phone isn't itself on WhatsApp, so a
    school linked a different real WhatsApp number to the same student
    (see Student.whatsapp_phone / PATCH /admin/students/{id}). A message
    from that alternate number routes to the SAME existing student, never
    creates a new profile.
    """
    students = (
        db.query(Student)
        .filter(or_(Student.phone == from_phone, Student.whatsapp_phone == from_phone))
        .order_by(Student.id)
        .all()
    )

    if not students:
        # A registered teacher/admin's own phone, messaging the bot cold
        # (never opened "My AI Tutor" on the web portal first, so no
        # linked Student profile exists yet) — confirmed live this was a
        # real, pre-existing gap: falling through to _create_new_student
        # created a completely disconnected "New Student" profile with
        # is_staff_profile=False and centre_id defaulting to the generic
        # Qlass Direct pool, not their real school, with no connection to
        # their actual teacher identity at all. Route to (or lazily
        # create) their real personal tutor profile instead — same call
        # GET /admin/my-tutor itself makes.
        teacher = db.query(Teacher).filter(Teacher.phone == from_phone).first()
        if teacher:
            return tenancy.get_or_create_linked_student(db, teacher.phone, teacher.name, teacher.centre_id), None
        return await _create_new_student(db, from_phone, message_text), None

    # The overwhelming majority of phones have exactly one profile — no
    # disambiguation is possible, so there's nothing for an LLM call to
    # resolve. Confirmed live this was NOT actually happening: the call
    # below fired unconditionally for every text message regardless of
    # student count, including messages from a student already blocked
    # by the credit gate further down _handle_message (that gate runs
    # AFTER this function returns) — silently spending real AI credit on
    # every single message a single-profile student ever sent, contrary
    # to this function's own original claim of "zero extra overhead" for
    # this case. The one capability this trades away is auto-detecting
    # "actually, add my other child" typed as a plain message on a phone
    # that currently has only one profile — a second profile can still be
    # added via the web signup flow, which immediately makes future
    # messages go through the real (len(students) > 1) disambiguation
    # path below as normal.
    if len(students) == 1:
        return students[0], None

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
    point Wati's dashboard webhook setting at this URL. The secret can be
    supplied either as a custom Authorization header (see Wati's Webhook
    settings) or as a `?secret=...` query parameter baked directly into the
    URL registered with Wati — see verify_webhook_auth for why both exist.
    """
    if not verify_webhook_auth(request.headers.get("authorization"), request.query_params.get("secret")):
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


def _canonical_lock_phone(db: Session, from_phone: str) -> str:
    """
    Lock key for a student's billing critical section. Normally just
    from_phone, but a student can have a distinct whatsapp_phone from their
    login phone (see Student.whatsapp_phone) — if from_phone matches
    someone's whatsapp_phone rather than their login phone, resolve to that
    real `phone` instead, so a concurrent web/app request (which always
    locks on `phone`, see app.routers.student_app._reply_to) and a WhatsApp
    request from their alternate number serialize against the SAME lock.
    Confirmed live this split could otherwise let the two channels overdraft
    a wallet the same way the my-tutor endpoints could before that fix (see
    app.routers.admin._my_tutor_reply).
    """
    student = (
        db.query(Student)
        .filter(Student.whatsapp_phone == from_phone, Student.phone != from_phone)
        .first()
    )
    return student.phone if student else from_phone


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
        lock_phone = _canonical_lock_phone(db, phone) if phone else None
        async with (student_lock(lock_phone) if lock_phone else nullcontext()):
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
    from_phone = payload.get("waId")
    if not from_phone:
        return

    # Handled before rate limiting / student resolution — Qlass staff
    # tapping Approve/Reject on the school_onboarding_review_alert template
    # aren't students at all, so routing this through the normal tutoring
    # pipeline would (at best) spuriously create a "student" profile for a
    # staff number, or (at worst) get rate-limited/misrouted like ordinary
    # chat traffic. See _handle_school_review_button.
    early_button_reply = parse_incoming_button_reply(payload)
    if early_button_reply:
        early_from_phone, early_button_text = early_button_reply
        review_action = SCHOOL_REVIEW_BUTTON_ACTIONS.get(early_button_text)
        if review_action:
            if early_from_phone in SCHOOL_REVIEW_STAFF_PHONES:
                await _handle_school_review_button(db, early_from_phone, review_action)
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
        command = (
            MENU_BUTTON_TO_COMMAND.get(button_text)
            or CREDIT_BUTTON_TO_COMMAND.get(button_text)
            or LEVEL_OFFER_BUTTON_TO_COMMAND.get(button_text)
            or LEVEL_CHOICE_BUTTON_TO_COMMAND.get(button_text, button_text)
        )
        parsed = (button_from_phone, command)
    else:
        parsed = parse_incoming_message(payload)
    probe_text = parsed[1] if parsed else ""

    # Confirmed live (code review, Aug 2026): a cold WhatsApp message from
    # any never-seen-before number silently creates a fresh account with a
    # real ₹50 trial-credit grant, with nothing bounding how many distinct
    # numbers could each claim one. Only checked for a genuinely new phone
    # (an existing student/teacher must never be blocked from chatting by
    # this) — global, not per-phone, since a per-phone limit can't stop an
    # attacker with many different throwaway numbers, which is exactly the
    # abuse this guards against (see rate_limit.is_signup_rate_limited).
    is_known_phone = (
        db.query(Student.id).filter(or_(Student.phone == from_phone, Student.whatsapp_phone == from_phone)).first()
        is not None
        or db.query(Teacher.id).filter(Teacher.phone == from_phone).first() is not None
    )
    if not is_known_phone and await is_signup_rate_limited("whatsapp_signup"):
        await send_whatsapp_message(
            from_phone, "We're seeing unusually high signup traffic right now — please try again in a few minutes!"
        )
        return

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

    # Same reasoning as "top up credits" above, and the SAME bug it exists
    # to prevent: without this, tapping "🏫 Ask My School" (which maps to
    # this exact phrase — see CREDIT_BUTTON_TO_COMMAND) fell straight
    # back into the wallet-balance gate below and just re-showed the same
    # "out of credits" notice in an unbreakable loop — confirmed live, a
    # student tapped it three times and got the identical notice every
    # time, never actually reaching their teacher. Notifying a teacher
    # costs no AI credits either, so it must bypass this gate too, not
    # just the later monthly-limit gate the teacher_help intent already
    # bypasses further down (that one never even gets a chance to run —
    # this wallet check happens before intent classification at all).
    if probe_text == "talk to teacher":
        recipients = get_escalation_recipients(db, student.centre_id)
        for recipient in recipients:
            await send_whatsapp_message(recipient.phone, format_student_requested_help_message(student.name))
        # A self-signup student (default "Qlass Direct" centre) has no
        # real school/teacher on file at all — confirmed live, this sent
        # "I've let your teacher know" to a student whose centre had zero
        # registered teachers, notifying literally nobody while claiming
        # otherwise. Only make that claim when someone was actually
        # notified; otherwise point to a real human via Call Us instead.
        reply = (
            "I've let your teacher know you'd like some help! 🙋 They'll reach out soon."
            if recipients
            else f"You're not linked to a school on our records, so I can't reach a teacher for you — "
                 f"call Qlass support directly at {QLASS_SUPPORT_PHONE} instead!"
        )
        await send_whatsapp_message(from_phone, reply)
        return

    if probe_text == "call qlass":
        await send_whatsapp_message(from_phone, f"You can call Qlass support directly at {QLASS_SUPPORT_PHONE} 📞")
        return

    # "stop nudges"/"unsubscribe" opts out of proactive re-engagement
    # messages (see app.services.nudges/scripts/send_engagement_nudges.py)
    # — checked here, before every other gate, same reasoning as the other
    # probe_text short-circuits above: an opt-out request must always work,
    # even for a churned/pilot-expired/out-of-credit student. Never affects
    # real tutoring replies, only this one unprompted-outreach feature.
    if probe_text in ("stop nudges", "unsubscribe"):
        student.nudges_opt_out = True
        db.commit()
        await send_whatsapp_message(
            from_phone, "Got it — you won't get any more check-in messages from me. You can still chat anytime you like! 👋"
        )
        return

    # A tap on the engagement nudge template's "Know More" Quick Reply
    # button (see app.services.nudges/scripts/send_engagement_nudges.py) —
    # arrives as plain button-title text, not through MENU_BUTTON_TO_COMMAND
    # (this button belongs to an external WhatsApp template, not our own
    # interactive send), so it's matched directly here rather than via that
    # dict. Case-insensitive since Wati echoes back the exact button title
    # rather than anything this codebase controls the casing of.
    #
    # The button is on every nudge send regardless of type (one shared
    # template, no per-send conditional button) but only means something
    # for a "fun_fact" nudge — get_know_more_reply returns None for a
    # feature_highlight/social_proof nudge (a pitch, not knowledge), and
    # this deliberately does NOT `return` in that case: the tap just falls
    # through into the normal flow below and gets handled like any other
    # message, rather than forcing a canned reply that wouldn't fit.
    if probe_text.strip().lower() == nudges.KNOW_MORE_BUTTON_TEXT.lower():
        know_more_reply = nudges.get_know_more_reply(student)
        if know_more_reply:
            await send_whatsapp_message(from_phone, know_more_reply)
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
        #
        # An unlimited-plan student reaching this point has burned through
        # their day/week/month flat-fee allotment (see
        # cost_tracker.is_unlimited_over_period_cap) AND has no wallet
        # balance to draw on — the generic "out of AI credits" wording
        # would be confusing for someone who's already paying a flat fee,
        # so this is the one thing that differs for them: what buying more
        # here actually means (topping up "usage credits" to keep going
        # until their plan's allotment resets, not paying for the plan
        # itself again).
        if cost_tracker.is_unlimited_active(student):
            notice = "You've used up your plan's included AI usage for this period — top up usage credits to keep going until it resets:"
        else:
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

    result = await process_message(db, student, message_text)

    if not result.reply_text:
        # A newer message from this same student already arrived while this
        # one was being processed — chat_core already skipped generating a
        # reply for this stale message (it's already in chat_history, so the
        # newer message's own reply sees it as context and responds to the
        # whole exchange at once). Nothing to send.
        return

    if result.menu_buttons:
        button_result = await send_whatsapp_buttons(from_phone, result.reply_text, result.menu_buttons)
        if not button_result.get("sent"):
            await send_whatsapp_message(from_phone, result.reply_text)
        return

    if result.level_offer:
        button_result = await send_whatsapp_buttons(
            from_phone, result.reply_text, _level_offer_buttons(result.level_offer)
        )
        if not button_result.get("sent"):
            await send_whatsapp_message(from_phone, result.reply_text)
        return

    if result.level_choice:
        button_result = await send_whatsapp_buttons(
            from_phone, result.reply_text, _level_choice_buttons(result.level_choice)
        )
        if not button_result.get("sent"):
            await send_whatsapp_message(from_phone, result.reply_text)
        return

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
    if result.wants_audio_reply:
        speaker = OPPOSITE_GENDER_SPEAKER.get(student.gender)
        audio_reply = await synthesize_speech(result.reply_text, result.detected_lang, speaker=speaker)
        if audio_reply:
            cost_tracker.record_char_usage(db, "sarvam_tts", len(result.reply_text), student.id)
            send_result = await send_whatsapp_audio(from_phone, audio_reply)
            if not send_result.get("sent"):
                logger.error("send_whatsapp_audio failed for %s: %s", from_phone, send_result)
            else:
                # Also send the text transcript, in the same language as the
                # voice note, so the student has it in writing too.
                await send_whatsapp_message(from_phone, result.reply_text)
    if result.image_prompt:
        image_bytes = await generate_image(result.image_prompt)
        if image_bytes:
            cost_tracker.record_flat_usage(db, "azure_image", student.id)
            image_result = await send_whatsapp_image(from_phone, image_bytes, caption=result.reply_text)
            if not image_result.get("sent"):
                logger.error("send_whatsapp_image failed for %s: %s", from_phone, image_result)
            elif not send_result:
                # Voice reply (if any) already covered the spoken explanation;
                # the image send itself counts as having delivered something,
                # so only treat this as the "send_result" when there wasn't
                # already a successful voice send above.
                send_result = image_result
    if not send_result or not send_result.get("sent"):
        fallback_result = await send_whatsapp_message(from_phone, result.reply_text)
        if not fallback_result.get("sent"):
            logger.error("send_whatsapp_message fallback also failed for %s: %s", from_phone, fallback_result)

    if result.video:
        # Sent as its own follow-up message (Wati has no native rich video
        # embed) rather than folded into the main reply, so the link
        # doesn't get lost inside a long translated paragraph.
        video_result = await send_whatsapp_message(from_phone, f"📺 {result.video['title']}\n{result.video['url']}")
        if not video_result.get("sent"):
            logger.error("send video suggestion failed for %s: %s", from_phone, video_result)
