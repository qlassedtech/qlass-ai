from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core import Parent, Student
from app.services import cost_tracker
from app.services.otp import generate_and_store_otp, verify_otp
from app.services.parent_auth import create_parent_access_token, get_current_parent
from app.services.progress_report import get_student_stats, get_activity_stats, get_chapter_coverage
from app.services.rate_limit import is_otp_rate_limited
from app.services.whatsapp_client import send_whatsapp_message

router = APIRouter()


class PhoneRequest(BaseModel):
    phone: str


@router.post("/parent-app/auth/request-otp")
async def request_parent_otp(body: PhoneRequest, db: Session = Depends(get_db)):
    """
    Unlike students, parents don't self-signup — a school links a parent's
    phone to their child's profile first (see PATCH /admin/students/{id}),
    so an unrecognized phone here just means "not linked yet," not "create
    a new account."
    """
    if await is_otp_rate_limited("parent_login_request", body.phone):
        raise HTTPException(status_code=429, detail="Too many requests — please wait a while before trying again")
    parent = db.query(Parent).filter(Parent.phone == body.phone).first()
    if not parent:
        raise HTTPException(
            status_code=404, detail="This number isn't linked to a student yet — ask your school to add it"
        )
    otp = await generate_and_store_otp("parent_login", body.phone)
    await send_whatsapp_message(body.phone, f"Your Qlass Learning parent login code is *{otp}*. It expires in 10 minutes.")
    return {"sent": True}


class VerifyOtpRequest(BaseModel):
    phone: str
    otp: str


@router.post("/parent-app/auth/verify-otp")
async def verify_parent_otp(body: VerifyOtpRequest, db: Session = Depends(get_db)):
    if await is_otp_rate_limited("parent_login_verify", body.phone):
        raise HTTPException(status_code=429, detail="Too many attempts — please request a new code")
    if not await verify_otp("parent_login", body.phone, body.otp):
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    parent = db.query(Parent).filter(Parent.phone == body.phone).first()
    if not parent:
        raise HTTPException(status_code=404, detail="Parent account not found")

    token = create_parent_access_token(parent.id)
    return {"access_token": token, "parent": {"id": parent.id, "name": parent.name}}


def _linked_student(db: Session, parent: Parent) -> Student:
    student = db.query(Student).filter(Student.id == parent.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Linked student not found")
    return student


@router.get("/parent-app/me")
def get_me(db: Session = Depends(get_db), parent: Parent = Depends(get_current_parent)):
    student = _linked_student(db, parent)
    return {
        "parent_name": parent.name,
        "student_name": student.name,
        "student_phone": student.phone,
        "class": student.class_,
        "credit_balance": cost_tracker.get_balance(db, student.id),
    }


@router.get("/parent-app/progress")
def get_progress(db: Session = Depends(get_db), parent: Parent = Depends(get_current_parent)):
    student = _linked_student(db, parent)
    stats = get_student_stats(db, student.id)
    activity = get_activity_stats(db, student.id)
    coverage = get_chapter_coverage(db, student)
    return {"stats": stats, "activity": activity, "coverage": coverage}
