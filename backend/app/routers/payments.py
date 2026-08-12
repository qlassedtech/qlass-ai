from datetime import datetime, timedelta, timezone

import razorpay
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.core import Student
from app.services import cost_tracker, razorpay_client
from app.services.otp import generate_and_store_otp, verify_otp
from app.services.rate_limit import is_otp_rate_limited
from app.services.razorpay_client import client as _client, MIN_TOPUP_AMOUNT
from app.services.whatsapp_client import send_whatsapp_message

router = APIRouter()


def _find_real_student(db: Session, phone: str, student_id: int | None = None) -> Student:
    """
    Excludes staff "My AI Tutor" profiles. When a shared family phone has
    more than one real student profile, student_id disambiguates which one
    a payment is actually for — every link this app generates already
    knows the specific student (a teacher/admin sending a top-up link, the
    WhatsApp bot replying to the exact student who's texting, a parent
    portal scoped to one linked child) and now includes it, so a real
    payment is never blindly attributed to "whichever sibling has the
    lowest id" — confirmed as a genuine risk: a family with two children
    sharing one phone could have a payment meant for the younger child
    silently credit the older one's wallet instead, purely because of
    database insertion order. student_id must still match phone (a
    student can't be claimed by an unrelated number), so this only ever
    narrows within that phone's own real profiles, never widens access.
    Falls back to the old lowest-id behavior only when no student_id is
    given at all (an older cached link, or a caller with no specific
    student in hand).
    """
    query = db.query(Student).filter(Student.phone == phone, Student.is_staff_profile.is_(False))
    if student_id is not None:
        student = query.filter(Student.id == student_id).first()
        if not student:
            raise HTTPException(
                status_code=404,
                detail="That student profile doesn't match this WhatsApp number — ask your school to check the link",
            )
        return student
    student = query.order_by(Student.id).first()
    if not student:
        raise HTTPException(
            status_code=404,
            detail="We couldn't find a student with that number — ask your school to enroll you first",
        )
    return student


class CreateOrderRequest(BaseModel):
    phone: str
    # gt/lt reject the non-standard JSON tokens Infinity/-Infinity/NaN
    # (stdlib json.loads accepts them; Python's `inf < MIN_TOPUP_AMOUNT` and
    # `nan < MIN_TOPUP_AMOUNT` both evaluate False, so a bare lower-bound
    # check below let either through to `int(round(body.amount * 100))`,
    # which raises an unhandled OverflowError/ValueError — an easy 500).
    # An upper bound also caps how large a single top-up order can be.
    amount: float = Field(gt=0, lt=1_000_000)
    student_id: int | None = None


@router.post("/pay/create-order")
def create_order(body: CreateOrderRequest, db: Session = Depends(get_db)):
    if _client is None:
        raise HTTPException(status_code=503, detail="Payments aren't configured yet — contact Qlass support")
    if body.amount < MIN_TOPUP_AMOUNT:
        raise HTTPException(status_code=400, detail=f"Minimum top-up is ₹{MIN_TOPUP_AMOUNT:.0f}")
    student = _find_real_student(db, body.phone, body.student_id)

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
    student_id: int | None = None


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

    student = _find_real_student(db, body.phone, body.student_id)

    # Re-fetch the order from Razorpay itself rather than trusting any
    # client-supplied amount. Also bind the server-side order notes and
    # captured payment to this exact student: a valid signature alone must
    # never let one customer's payment credit another student's wallet.
    order = _client.order.fetch(body.razorpay_order_id)
    payment = _client.payment.fetch(body.razorpay_payment_id)
    try:
        razorpay_client.require_paid_order(
            order, payment, {"student_id": str(student.id), "phone": student.phone}
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Payment does not match this student") from exc

    # Check idempotency only after the ownership binding above, so a replay
    # cannot use an already-credited payment to probe another student's
    # balance.
    if cost_tracker.has_processed_external_ref(db, body.razorpay_payment_id):
        return {"credited": 0.0, "balance": cost_tracker.get_balance(db, student.id)}
    amount_inr = order["amount"] / 100

    new_balance = cost_tracker.add_credits(
        db, student.id, amount_inr, note=f"Razorpay payment {body.razorpay_payment_id}",
        external_ref=body.razorpay_payment_id,
    )
    return {"credited": amount_inr, "balance": new_balance}


class CreateSubscriptionRequest(BaseModel):
    phone: str
    student_id: int | None = None


@router.post("/pay/create-subscription")
def create_student_subscription(body: CreateSubscriptionRequest, db: Session = Depends(get_db)):
    """
    Self-serve recurring auto-renewal for the ₹2499/yr unlimited plan —
    distinct from /pay/create-order's one-time top-up. Uses Razorpay's
    Subscriptions API (a recurring mandate the student/parent authorizes
    once via UPI Autopay or card auto-debit), not the Orders API. The
    FIRST payment is confirmed via /pay/verify-subscription below; every
    renewal after that is handled entirely by the webhook
    (app.routers.razorpay_webhook), with no user action needed.
    """
    if _client is None or not settings.razorpay_student_plan_id:
        raise HTTPException(status_code=503, detail="Subscriptions aren't configured yet — contact Qlass support")
    student = _find_real_student(db, body.phone, body.student_id)
    if cost_tracker.is_unlimited_active(student):
        raise HTTPException(status_code=400, detail="This student is already on the unlimited plan")

    subscription = razorpay_client.create_subscription(
        settings.razorpay_student_plan_id, razorpay_client.STUDENT_SUBSCRIPTION_TOTAL_CYCLES,
        notes={"student_id": str(student.id), "phone": student.phone, "kind": "student_annual"},
    )
    return {"subscription_id": subscription["id"], "key_id": settings.razorpay_key_id}


class VerifySubscriptionRequest(BaseModel):
    razorpay_subscription_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    phone: str
    student_id: int | None = None


@router.post("/pay/verify-subscription")
def verify_student_subscription(body: VerifySubscriptionRequest, db: Session = Depends(get_db)):
    """Confirms the FIRST payment on a new subscription mandate — see create_student_subscription."""
    if _client is None:
        raise HTTPException(status_code=503, detail="Subscriptions aren't configured yet — contact Qlass support")

    try:
        _client.utility.verify_subscription_payment_signature({
            "razorpay_subscription_id": body.razorpay_subscription_id,
            "razorpay_payment_id": body.razorpay_payment_id,
            "razorpay_signature": body.razorpay_signature,
        })
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Payment could not be verified")

    student = _find_real_student(db, body.phone, body.student_id)
    subscription = razorpay_client.fetch_subscription(body.razorpay_subscription_id)
    payment = _client.payment.fetch(body.razorpay_payment_id)
    try:
        razorpay_client.require_active_subscription(
            subscription,
            payment,
            {"student_id": str(student.id), "phone": student.phone, "kind": "student_annual"},
            settings.razorpay_student_plan_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Subscription does not match this student") from exc

    if cost_tracker.has_processed_external_ref(db, body.razorpay_payment_id):
        return {
            "subscription_plan": student.subscription_plan,
            "subscription_expires_at": student.subscription_expires_at,
        }

    student.subscription_plan = "unlimited"
    student.subscription_expires_at = datetime.now(timezone.utc) + timedelta(
        days=cost_tracker.UNLIMITED_STUDENT_ANNUAL_DAYS
    )
    student.razorpay_subscription_id = body.razorpay_subscription_id
    cost_tracker.add_credits(
        db, student.id, cost_tracker.UNLIMITED_STUDENT_ANNUAL_PRICE,
        note="Razorpay subscription — first payment", external_ref=body.razorpay_payment_id,
        service="unlimited_plan_recurring",
    )
    db.commit()
    return {"subscription_plan": student.subscription_plan, "subscription_expires_at": student.subscription_expires_at}


@router.post("/pay/request-cancel-otp")
async def request_cancel_subscription_otp(body: CreateSubscriptionRequest, db: Session = Depends(get_db)):
    """
    Step 1 of cancelling an auto-renewal mandate — sends a WhatsApp OTP to
    the student's own phone. Confirmed live: cancel-subscription previously
    took just {phone, student_id} with nothing else, and student_id is a
    small sequential integer that's already visible in this same file's own
    top-up links (see _find_real_student's docstring) — anyone who'd seen a
    student's payment/top-up link (routinely forwarded over WhatsApp) could
    silently kill a paying family's subscription. This proves the caller
    actually has the phone in hand before cancel-subscription below acts.
    """
    if await is_otp_rate_limited("subscription_cancel_request", body.phone):
        raise HTTPException(status_code=429, detail="Too many requests — please wait a while before trying again")
    student = _find_real_student(db, body.phone, body.student_id)
    if not student.razorpay_subscription_id:
        raise HTTPException(status_code=400, detail="No active auto-renewing subscription found for this student")
    otp = await generate_and_store_otp("subscription_cancel", body.phone)
    await send_whatsapp_message(
        body.phone, f"Your code to cancel your Qlass AI Tutor subscription is *{otp}*. It expires in 10 minutes."
    )
    return {"sent": True}


class CancelSubscriptionRequest(CreateSubscriptionRequest):
    otp: str


@router.post("/pay/cancel-subscription")
async def cancel_student_subscription(body: CancelSubscriptionRequest, db: Session = Depends(get_db)):
    """
    Self-serve cancellation of the auto-renewal mandate — step 2, after
    /pay/request-cancel-otp. Cancelling stops future charges but doesn't
    immediately revoke access — the student keeps their unlimited plan
    until subscription_expires_at (the period they already paid for),
    matching how cancelling any subscription normally works. That expiry is
    picked up naturally by the existing has_credits/is_unlimited_active
    check once it passes.
    """
    if _client is None:
        raise HTTPException(status_code=503, detail="Subscriptions aren't configured yet — contact Qlass support")
    if await is_otp_rate_limited("subscription_cancel_verify", body.phone):
        raise HTTPException(status_code=429, detail="Too many attempts — please request a new code")
    if not await verify_otp("subscription_cancel", body.phone, body.otp):
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    student = _find_real_student(db, body.phone, body.student_id)
    if not student.razorpay_subscription_id:
        raise HTTPException(status_code=400, detail="No active auto-renewing subscription found for this student")
    razorpay_client.cancel_subscription(student.razorpay_subscription_id)
    student.razorpay_subscription_id = None
    db.commit()
    return {"cancelled": True, "access_until": student.subscription_expires_at}
