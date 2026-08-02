from pydantic import BaseModel
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core import ChatHistory, Parent, Student, Teacher
from app.services import cost_tracker, school_billing
from app.services.audio_qa import detect_gender_from_pitch, get_duration_seconds
from app.services.document_client import extract_text_from_document
from app.services.ocr_client import extract_text_from_image
from app.services.otp import generate_and_store_otp, verify_otp
from app.services.progress_report import get_activity_stats, get_chapter_coverage, get_student_stats
from app.services.rate_limit import is_otp_rate_limited
from app.services.referral import REFERRAL_SIGNUP_BONUS
from app.services.sarvam_client import transcribe_audio
from app.services.student_auth import create_student_access_token, get_current_student
from app.services.student_chat import process_web_message
from app.services.tenancy import create_student_profile, get_qlass_direct_centre_id
from app.services.whatsapp_client import send_whatsapp_message

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
      - anything else -> "otp" (a plain student, WhatsApp OTP, no
        password ever exists for these accounts) into the student chat app
    Checked in this order since a phone could theoretically appear in more
    than one role over time (e.g. a teacher who later becomes a parent
    contact) — teacher login always takes priority.
    """
    if db.query(Teacher).filter(Teacher.phone == body.phone).first():
        return {"login_type": "password"}
    if db.query(Parent).filter(Parent.phone == body.phone).first():
        return {"login_type": "parent_otp"}
    return {"login_type": "otp"}


@router.post("/student-app/auth/request-otp")
async def request_otp(body: CheckPhoneRequest, db: Session = Depends(get_db)):
    if await is_otp_rate_limited("student_login_request", body.phone):
        raise HTTPException(status_code=429, detail="Too many requests — please wait a while before trying again")
    if db.query(Teacher).filter(Teacher.phone == body.phone).first():
        raise HTTPException(status_code=400, detail="This number has a teacher account — use password login instead")
    if db.query(Parent).filter(Parent.phone == body.phone).first():
        raise HTTPException(status_code=400, detail="This number is registered as a parent contact — use the parent login instead")
    otp = await generate_and_store_otp("student_login", body.phone)
    await send_whatsapp_message(body.phone, f"Your Qlass Learning login code is *{otp}*. It expires in 10 minutes.")
    return {"sent": True}


class VerifyOtpRequest(BaseModel):
    phone: str
    otp: str
    referral_code: str | None = None
    name: str | None = None


@router.post("/student-app/auth/verify-otp")
async def verify_student_otp(body: VerifyOtpRequest, db: Session = Depends(get_db)):
    if await is_otp_rate_limited("student_login_verify", body.phone):
        raise HTTPException(status_code=429, detail="Too many attempts — please request a new code")
    if not await verify_otp("student_login", body.phone, body.otp):
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    student = (
        db.query(Student)
        .filter(Student.phone == body.phone, Student.is_staff_profile.is_(False))
        .order_by(Student.id.desc())
        .first()
    )
    if not student:
        student = create_student_profile(db, body.phone, body.name or "New Student", get_qlass_direct_centre_id(db))
        if body.referral_code:
            referrer = db.query(Student).filter(Student.referral_code == body.referral_code).first()
            if referrer:
                student.referred_by_id = referrer.id
                db.commit()
                cost_tracker.grant_referral_credit(
                    db, referrer.id, REFERRAL_SIGNUP_BONUS, note="Referral milestone: signup",
                )

    token = create_student_access_token(student.id)
    return {"access_token": token, "student": student_summary(db, student)}


@router.get("/student-app/me")
def get_me(db: Session = Depends(get_db), student: Student = Depends(get_current_student)):
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
        raise HTTPException(status_code=402, detail="You're out of AI credits — ask your school to top up your account")
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
