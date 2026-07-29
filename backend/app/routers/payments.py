import razorpay
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.core import Student
from app.services import cost_tracker
from app.services.razorpay_client import client as _client, MIN_TOPUP_AMOUNT

router = APIRouter()


class CreateOrderRequest(BaseModel):
    phone: str
    amount: float


@router.post("/pay/create-order")
def create_order(body: CreateOrderRequest, db: Session = Depends(get_db)):
    if _client is None:
        raise HTTPException(status_code=503, detail="Payments aren't configured yet — contact Qlass support")
    if body.amount < MIN_TOPUP_AMOUNT:
        raise HTTPException(status_code=400, detail=f"Minimum top-up is ₹{MIN_TOPUP_AMOUNT:.0f}")

    # Excludes staff "My AI Tutor" profiles and picks deterministically
    # (lowest id) when a shared family phone has more than one real
    # student profile — see Student.is_staff_profile and
    # app.services.active_profile for the richer WhatsApp-side
    # disambiguation this simpler public page doesn't yet have.
    student = (
        db.query(Student)
        .filter(Student.phone == body.phone, Student.is_staff_profile.is_(False))
        .order_by(Student.id)
        .first()
    )
    if not student:
        raise HTTPException(
            status_code=404,
            detail="We couldn't find a student with that number — ask your school to enroll you first",
        )

    # Amount in paise (Razorpay's smallest unit), matching how the amount
    # will be re-fetched from Razorpay's own API at verification time —
    # never trust a client-supplied amount for the actual credit grant.
    order = _client.order.create({
        "amount": int(round(body.amount * 100)),
        "currency": "INR",
        "notes": {"student_id": str(student.id), "phone": student.phone},
    })
    return {"order_id": order["id"], "key_id": settings.razorpay_key_id, "amount": order["amount"], "currency": "INR"}


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    phone: str


@router.post("/pay/verify")
def verify_payment(body: VerifyPaymentRequest, db: Session = Depends(get_db)):
    if _client is None:
        raise HTTPException(status_code=503, detail="Payments aren't configured yet — contact Qlass support")

    try:
        _client.utility.verify_payment_signature({
            "razorpay_order_id": body.razorpay_order_id,
            "razorpay_payment_id": body.razorpay_payment_id,
            "razorpay_signature": body.razorpay_signature,
        })
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Payment could not be verified")

    # Excludes staff "My AI Tutor" profiles and picks deterministically
    # (lowest id) when a shared family phone has more than one real
    # student profile — see Student.is_staff_profile and
    # app.services.active_profile for the richer WhatsApp-side
    # disambiguation this simpler public page doesn't yet have.
    student = (
        db.query(Student)
        .filter(Student.phone == body.phone, Student.is_staff_profile.is_(False))
        .order_by(Student.id)
        .first()
    )
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # Guards against crediting the same payment twice — a client retry
    # after a slow/dropped response, or a replayed request with a still-
    # valid signature, would otherwise double-credit the wallet.
    if cost_tracker.has_processed_external_ref(db, body.razorpay_payment_id):
        return {"credited": 0.0, "balance": cost_tracker.get_balance(db, student.id)}

    # Re-fetch the order from Razorpay itself rather than trusting any
    # client-supplied amount, so a tampered request can't credit more than
    # was actually paid.
    order = _client.order.fetch(body.razorpay_order_id)
    amount_inr = order["amount"] / 100

    new_balance = cost_tracker.add_credits(
        db, student.id, amount_inr, note=f"Razorpay payment {body.razorpay_payment_id}",
        external_ref=body.razorpay_payment_id,
    )
    return {"credited": amount_inr, "balance": new_balance}
