from pydantic import BaseModel
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.business_rules import TUTOR_LEVEL_MODELS
from app.config import settings
from app.database import get_db
from app.models.core import ChatHistory, Parent, Student, Teacher
from app.services import cost_tracker, school_billing
from app.services.audio_qa import detect_gender_from_pitch, get_duration_seconds
from app.services.document_client import extract_text_from_document
from app.services.escalation import QLASS_SUPPORT_PHONE, get_escalation_recipients
from app.services.google_auth import GoogleAuthError, verify_google_id_token
from app.services.ocr_client import extract_text_from_image
from app.services.otp import generate_and_store_otp, verify_otp, LOGIN_OTP_TEMPLATE_NAME
from app.services.phone import normalize_phone
from app.services.progress_report import get_activity_stats, get_chapter_coverage, get_student_stats
from app.services.rate_limit import is_otp_rate_limited, student_lock
from app.services.referral import apply_referral_at_signup
from app.services.sarvam_client import transcribe_audio
from app.services.student_auth import create_student_access_token, get_current_student
from app.services.teacher_auth import verify_password
from app.services.student_chat import process_web_message
from app.services.tenancy import create_student_profile, get_qlass_direct_centre_id
from app.services.whatsapp_client import send_template_message

router = APIRouter()

# A long-running student could otherwise accumulate years of chat history —
# returning every row unbounded on every page load doesn't scale (grows the
# response and query cost indefinitely). Recent-first is what a "history"
# screen actually needs; a proper paginated "load more" is future work.
CHAT_HISTORY_LIMIT = 200


def student_summary(db: Session, student: Student) -> dict:
    return {
        "id": student.id,
        "name": student.name,
        "class": student.class_,
        "board": student.board,
        "focus_topic": student.focus_topic,
        "credit_balance": cost_tracker.get_balance(db, student.id),
        "referral_code": student.referral_code,
        "email": student.email,
        "tutor_level": student.tutor_level,
    }


class CheckPhoneRequest(BaseModel):
    phone: str


@router.post("/student-app/auth/check-phone")
def check_phone(body: CheckPhoneRequest, db: Session = Depends(get_db)):
    """
    Tells the unified login page which form to show:
      - a teacher/admin's own phone -> "password" (existing portal login,
        lands in the teacher portal, where "My AI Tutor" is available)
      - a phone a school has linked as a parent contact -> "parent_otp"
        (read-only progress + billing view for their child)
      - a student a school has given a portal password (see
        POST /admin/students/{id}/set-password — for a student with no
        WhatsApp access at all) -> "student_password"
      - anything else -> "otp" (a plain student, WhatsApp OTP, no
        password ever exists for these accounts) into the student chat app
    Checked in this order since a phone could theoretically appear in more
    than one role over time (e.g. a teacher who later becomes a parent
    contact) — teacher login always takes priority.
    """
    phone = normalize_phone(body.phone)
    if db.query(Teacher).filter(Teacher.phone == phone).first():
        return {"login_type": "password"}
    if db.query(Parent).filter(Parent.phone == phone).first():
        return {"login_type": "parent_otp"}
    if db.query(Student).filter(Student.phone == phone, Student.password_hash.isnot(None)).first():
        return {"login_type": "student_password"}
    return {"login_type": "otp"}


class StudentLoginRequest(BaseModel):
    phone: str
    password: str


@router.post("/student-app/auth/login")
def student_login(body: StudentLoginRequest, db: Session = Depends(get_db)):
    """
    Password login for a student with no WhatsApp access — sits alongside
    OTP login (see verify_student_otp below), never a replacement; a
    student who does have WhatsApp still uses OTP as normal, since only a
    school admin/teacher can set a password for a student in the first
    place (see POST /admin/students/{id}/set-password).
    """
    phone = normalize_phone(body.phone)
    student = (
        db.query(Student)
        .filter(Student.phone == phone, Student.is_staff_profile.is_(False))
        .order_by(Student.id.desc())
        .first()
    )
    if not student or not student.password_hash or not verify_password(body.password, student.password_hash):
        raise HTTPException(status_code=401, detail="Invalid phone or password")
    token = create_student_access_token(student.id)
    return {"access_token": token, "student": student_summary(db, student)}


class StudentGoogleLoginRequest(BaseModel):
    id_token: str


@router.post("/student-app/auth/google-login")
def student_google_login(body: StudentGoogleLoginRequest, db: Session = Depends(get_db)):
    """
    Sign in with an already-linked Google account — see
    /student-app/auth/link-google, the only way that link gets created.
    Never creates a new student or grants trial credit; WhatsApp OTP stays
    the sole way to prove phone ownership and get credit in the first
    place (see app.routers.public.register/whatsapp._create_new_student).
    This is purely an alternate way back into an account that already
    exists, same reasoning as the teacher/admin equivalent
    (app.routers.admin.google_login).
    """
    try:
        payload = verify_google_id_token(body.id_token)
    except GoogleAuthError as exc:
        raise HTTPException(status_code=401, detail=f"Google sign-in failed: {exc}")
    if not payload.get("email_verified"):
        raise HTTPException(status_code=401, detail="Your Google account's email isn't verified")
    student = (
        db.query(Student)
        .filter(Student.email == payload["email"], Student.is_staff_profile.is_(False))
        .first()
    )
    if not student:
        raise HTTPException(
            status_code=404,
            detail="No account found for this Google email — sign in with your WhatsApp number first, "
                   "then link Google from your account settings",
        )
    token = create_student_access_token(student.id)
    return {"access_token": token, "student": student_summary(db, student)}


@router.post("/student-app/auth/link-google")
def link_google_account(
    body: StudentGoogleLoginRequest, db: Session = Depends(get_db), student: Student = Depends(get_current_student),
):
    """
    Links a Google account to the CALLER's own already-authenticated
    student account — requires a valid student session (phone/OTP or
    password login), so this can only ever attach Google to an account
    whose phone was already verified, never bypass that verification.
    """
    try:
        payload = verify_google_id_token(body.id_token)
    except GoogleAuthError as exc:
        raise HTTPException(status_code=401, detail=f"Google sign-in failed: {exc}")
    if not payload.get("email_verified"):
        raise HTTPException(status_code=401, detail="Your Google account's email isn't verified")
    email = payload["email"]
    existing = db.query(Student).filter(Student.email == email, Student.id != student.id).first()
    if existing:
        raise HTTPException(status_code=409, detail="This Google account is already linked to another student")
    student.email = email
    try:
        db.commit()
    except IntegrityError:
        # The check-then-write above has a real (if narrow) race: two
        # concurrent link-google calls for the same Google email — e.g. a
        # shared/synced account — can both pass the `existing is None`
        # check before either commits. The DB's own unique constraint on
        # Student.email is what actually prevents the double-link; this
        # just turns the resulting crash into the same 409 the sequential
        # case already returns, instead of an unhandled 500.
        db.rollback()
        raise HTTPException(status_code=409, detail="This Google account is already linked to another student")
    return {"linked": True, "email": email}


@router.post("/student-app/auth/request-otp")
async def request_otp(body: CheckPhoneRequest, db: Session = Depends(get_db)):
    phone = normalize_phone(body.phone)
    if await is_otp_rate_limited("student_login_request", phone):
        raise HTTPException(status_code=429, detail="Too many requests — please wait a while before trying again")
    if db.query(Teacher).filter(Teacher.phone == phone).first():
        raise HTTPException(status_code=400, detail="This number has a teacher account — use password login instead")
    if db.query(Parent).filter(Parent.phone == phone).first():
        raise HTTPException(status_code=400, detail="This number is registered as a parent contact — use the parent login instead")
    otp = await generate_and_store_otp("student_login", phone)
    # Logging into the web portal doesn't guarantee an active 24h WhatsApp
    # session with the bot — a plain send_whatsapp_message session message
    # can silently fail to deliver in that case, so this uses the approved
    # Authentication-category template instead (same fix as the teacher/
    # parent OTP flows). Must be send_template_message (singular endpoint),
    # not send_broadcast_template — see that function's docstring.
    result = await send_template_message(phone, LOGIN_OTP_TEMPLATE_NAME, [{"name": "1", "value": otp}])
    if not result.get("sent"):
        raise HTTPException(status_code=502, detail="Couldn't send the login code — please try again shortly")
    return {"sent": True}


class VerifyOtpRequest(BaseModel):
    phone: str
    otp: str
    referral_code: str | None = None
    name: str | None = None


@router.post("/student-app/auth/verify-otp")
async def verify_student_otp(body: VerifyOtpRequest, db: Session = Depends(get_db)):
    phone = normalize_phone(body.phone)
    if await is_otp_rate_limited("student_login_verify", phone):
        raise HTTPException(status_code=429, detail="Too many attempts — please request a new code")
    if not await verify_otp("student_login", phone, body.otp):
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    student = (
        db.query(Student)
        .filter(Student.phone == phone, Student.is_staff_profile.is_(False))
        .order_by(Student.id.desc())
        .first()
    )
    if not student:
        student = create_student_profile(db, phone, body.name or "New Student", get_qlass_direct_centre_id(db))
        await apply_referral_at_signup(db, student, body.referral_code)

    token = create_student_access_token(student.id)
    return {"access_token": token, "student": student_summary(db, student)}


@router.get("/student-app/me")
def get_me(db: Session = Depends(get_db), student: Student = Depends(get_current_student)):
    return student_summary(db, student)


class SetTutorLevelRequest(BaseModel):
    level: int


@router.post("/student-app/tutor-level")
def set_tutor_level(
    body: SetTutorLevelRequest, db: Session = Depends(get_db), student: Student = Depends(get_current_student),
):
    """
    A direct, structured way to switch tutor level from the web/mobile
    UI (a header control, no typing) — the WhatsApp equivalent is typing
    "level N" or tapping a Change Level button, both of which resolve
    through chat_core's own change_level intent instead, since there's no
    separate UI to hit an endpoint from there. Mirrors that path's own
    side effect: any outstanding 50%/75% auto-downgrade offer is cleared,
    since the student has now made an explicit choice that supersedes it
    — see chat_core.Student.pending_level_offer.
    """
    if body.level not in TUTOR_LEVEL_MODELS:
        raise HTTPException(status_code=400, detail="level must be between 1 and 4")
    student.tutor_level = body.level
    student.pending_level_offer = None
    db.commit()
    return student_summary(db, student)


@router.get("/student-app/chat/history")
def get_chat_history(db: Session = Depends(get_db), student: Student = Depends(get_current_student)):
    rows = (
        db.query(ChatHistory)
        .filter(ChatHistory.student_id == student.id)
        .order_by(ChatHistory.created_at.desc())
        .limit(CHAT_HISTORY_LIMIT)
        .all()
    )
    return [
        {"role": r.role, "message": r.message, "created_at": r.created_at.isoformat()} for r in reversed(rows)
    ]


@router.get("/student-app/progress")
def get_progress(db: Session = Depends(get_db), student: Student = Depends(get_current_student)):
    """
    JSON equivalent of the WhatsApp "progress" intent's format_progress_
    message (see app.routers.whatsapp) — same underlying stats functions,
    but structured for the app to render as a real progress screen instead
    of a single chat bubble of text.
    """
    stats = get_student_stats(db, student.id)
    activity = get_activity_stats(db, student.id)
    coverage = get_chapter_coverage(db, student)
    return {
        "total_evaluated": stats["total_evaluated"],
        "correct": stats["correct"],
        "incorrect": stats["incorrect"],
        "accuracy_pct": stats["accuracy_pct"],
        "weak_topics": stats["weak_topics"],
        "messages_sent": stats["messages_sent"],
        "streak_days": activity["streak_days"],
        "active_days": activity["active_days"],
        "chapters_covered": len(coverage["covered"]) if coverage else None,
        "chapters_total": coverage["total"] if coverage else None,
        "chapters_not_covered": coverage["not_covered"][:5] if coverage else [],
    }


@router.get("/student-app/credits/history")
def get_credit_history(db: Session = Depends(get_db), student: Student = Depends(get_current_student)):
    events = cost_tracker.get_credit_history(db, student.id)
    return [
        {
            "amount": float(e.amount), "service": e.service, "note": e.note,
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ]


class DeviceTokenRequest(BaseModel):
    token: str


@router.post("/student-app/device-token")
def register_device_token(
    body: DeviceTokenRequest, db: Session = Depends(get_db), student: Student = Depends(get_current_student),
):
    """
    Called by the Android app right after login (and whenever FCM rotates
    the token) so push notifications — currently only send_habit_nudges.py
    — can reach app users, not just WhatsApp. A no-op with nothing to send
    to until Firebase is actually configured (see app.services.push_client).
    """
    student.fcm_token = body.token
    db.commit()
    return {"saved": True}


class SendMessageRequest(BaseModel):
    message: str


async def _reply_to(db: Session, student: Student, message_text: str) -> dict:
    """
    Shared by every input channel this app supports (typed, photo, voice
    note, document) — same billing gates and same process_web_message call
    WhatsApp effectively goes through, so a student sees identical behavior
    regardless of which endpoint got them here.
    """
    async with student_lock(student.phone):
        return await _reply_to_locked(db, student, message_text)


async def _reply_to_locked(db: Session, student: Student, message_text: str) -> dict:
    """
    The actual credits-check-through-deduction critical section, run inside
    _reply_to's per-student lock. Confirmed live (code review, Aug 2026):
    unlike WhatsApp (where app.routers.whatsapp already serializes a
    student's turns with this same lock), this web endpoint had no lock at
    all — two requests arriving close together could both pass the
    has_credits() check below before either deduction committed, taking the
    balance further negative than a single overdraft should allow.
    """
    if school_billing.is_centre_churned(db, student.centre_id) and not cost_tracker.has_independent_payment(db, student.id):
        raise HTTPException(
            status_code=402,
            detail="Your school's Qlass account is currently on hold — ask your school to contact Qlass, "
                   "or top up your own AI credits directly",
        )
    # Mirrors the same gate whatsapp.py enforces — without it, a student
    # could keep chatting for free through the web app indefinitely past
    # their school's pilot expiry, since this endpoint never checked it.
    if school_billing.is_centre_pilot_expired(db, student.centre_id) and not cost_tracker.has_independent_payment(db, student.id):
        raise HTTPException(
            status_code=402,
            detail="Your school's Qlass pilot has ended. Ask your school to continue the programme, "
                   "or top up your own AI credits to keep learning!",
        )
    if not cost_tracker.has_credits(db, student.id):
        # Same real options WhatsApp's credit-exhausted button menu offers
        # (Top Up / Ask My School / Call Us — see app.routers.whatsapp),
        # just as plain text since this is a REST error body, not a
        # WhatsApp interactive message. "Ask your school" is only said
        # when a school can actually be notified — a self-signup student
        # with no real school on file (get_escalation_recipients returns
        # nothing for the "Qlass Direct" fallback centre) was otherwise
        # told to do something with no way to act on it.
        pay_link = f"{settings.portal_base_url}/pay?phone={student.phone}&student_id={student.id}"
        has_school = bool(get_escalation_recipients(db, student.centre_id))
        school_note = "ask your school to top up your account, or " if has_school else ""
        # An unlimited-plan student reaching this point has burned through
        # their day/week/month flat-fee allotment (see
        # cost_tracker.is_unlimited_over_period_cap) AND has no wallet
        # balance — "out of AI credits" would be confusing for someone
        # already paying a flat fee, so this is worded as topping up
        # "usage credits" to keep going until their plan resets, not
        # paying for the plan itself again (see app.routers.whatsapp for
        # the same distinction on WhatsApp).
        if cost_tracker.is_unlimited_active(student):
            detail = (
                f"You've used up your plan's included AI usage for this period — top up usage credits to "
                f"keep going until it resets: {pay_link} (or call Qlass support at {QLASS_SUPPORT_PHONE})"
            )
        else:
            detail = (
                f"You're out of AI credits — {school_note}top up directly: {pay_link} "
                f"(or call Qlass support at {QLASS_SUPPORT_PHONE})"
            )
        raise HTTPException(status_code=402, detail=detail)
    reply = await process_web_message(db, student, message_text)
    return {"reply": reply, "credit_balance": cost_tracker.get_balance(db, student.id)}


@router.post("/student-app/chat/send")
async def send_message(
    body: SendMessageRequest, db: Session = Depends(get_db), student: Student = Depends(get_current_student)
):
    return await _reply_to(db, student, body.message)


@router.post("/student-app/chat/send-image")
async def send_image_message(
    file: UploadFile = File(...), db: Session = Depends(get_db), student: Student = Depends(get_current_student),
):
    """
    Photo-of-a-question support (homework worksheets, textbook problems) —
    same Azure Vision OCR call WhatsApp uses (app.services.ocr_client), just
    fed from a multipart upload instead of a Wati media URL. The extracted
    text is then indistinguishable from typed input to everything downstream.
    """
    if not student.has_feature("ocr"):
        raise HTTPException(status_code=403, detail="Photo questions aren't available on your account yet")
    image_bytes = await file.read()
    message_text = await extract_text_from_image(image_bytes)
    if not message_text:
        raise HTTPException(status_code=422, detail="Couldn't read any text in that photo — try a clearer picture")
    cost_tracker.record_flat_usage(db, "azure_ocr", student.id)
    return await _reply_to(db, student, message_text)


@router.post("/student-app/chat/send-voice")
async def send_voice_message(
    file: UploadFile = File(...), db: Session = Depends(get_db), student: Student = Depends(get_current_student),
):
    """Same Sarvam STT call WhatsApp uses (app.services.sarvam_client) for a recorded voice note."""
    if not student.has_feature("voice"):
        raise HTTPException(status_code=403, detail="Voice questions aren't available on your account yet")
    audio_bytes = await file.read()
    message_text = await transcribe_audio(audio_bytes, filename=file.filename or "voice_note.m4a")
    if not message_text:
        raise HTTPException(status_code=422, detail="Couldn't understand that voice note — please try again")
    cost_tracker.record_minute_usage(db, "sarvam_stt", get_duration_seconds(audio_bytes) / 60, student.id)
    if student.gender is None:
        detected_gender = detect_gender_from_pitch(audio_bytes)
        if detected_gender:
            student.gender = detected_gender
            db.commit()
    return await _reply_to(db, student, message_text)


@router.post("/student-app/chat/send-document")
async def send_document_message(
    file: UploadFile = File(...), db: Session = Depends(get_db), student: Student = Depends(get_current_student),
):
    """
    PDF/Word worksheet upload — same extract_text_from_document WhatsApp
    uses. The extracted text goes through the normal message pipeline,
    which pins it as the student's active document (see
    app.services.document_client.apply_active_document_pin) so follow-up
    turns like "now question 5" keep working once it's out of the recent-
    history window, exactly as on WhatsApp.
    """
    if not student.has_feature("documents"):
        raise HTTPException(status_code=403, detail="PDF/Word file questions aren't available on your account yet")
    document_bytes = await file.read()
    message_text = extract_text_from_document(document_bytes, file.filename or "")
    if not message_text:
        raise HTTPException(status_code=422, detail="Couldn't read any text in that file — try a different file")
    return await _reply_to(db, student, message_text)
