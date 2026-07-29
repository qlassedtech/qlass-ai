from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core import ChatHistory, Parent, Student, Teacher
from app.services import cost_tracker
from app.services.otp import generate_and_store_otp, verify_otp
from app.services.rate_limit import is_otp_rate_limited
from app.services.referral import REFERRAL_SIGNUP_BONUS
from app.services.student_auth import create_student_access_token, get_current_student
from app.services.student_chat import process_web_message
from app.services.tenancy import create_student_profile, get_qlass_direct_centre_id
from app.services.whatsapp_client import send_whatsapp_message

router = APIRouter()


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
        .order_by(ChatHistory.created_at.asc())
        .all()
    )
    return [{"role": r.role, "message": r.message, "created_at": r.created_at.isoformat()} for r in rows]


class SendMessageRequest(BaseModel):
    message: str


@router.post("/student-app/chat/send")
async def send_message(
    body: SendMessageRequest, db: Session = Depends(get_db), student: Student = Depends(get_current_student)
):
    if not cost_tracker.has_credits(db, student.id):
        raise HTTPException(status_code=402, detail="You're out of AI credits — ask your school to top up your account")
    reply = await process_web_message(db, student, body.message)
    return {"reply": reply, "credit_balance": cost_tracker.get_balance(db, student.id)}
