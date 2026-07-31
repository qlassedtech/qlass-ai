import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.core import Student
from app.services import cost_tracker, razorpay_client
from app.services.whatsapp_client import send_whatsapp_message

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/webhook/razorpay/subscriptions")
async def handle_razorpay_subscription_webhook(request: Request):
    """
    Receives Razorpay Subscription lifecycle events — this is what makes
    the unlimited plan's auto-renewal actually automatic, rather than
    someone manually re-running the activation endpoint every period (see
    app.routers.payments/admin's create-subscription endpoints for how the
    mandate is first authorized).

    NOT YET REGISTERED with Razorpay — this needs a real public HTTPS URL
    (only exists once deployed to a real server) and the resulting webhook
    secret pasted into RAZORPAY_WEBHOOK_SECRET; until both of those happen
    this endpoint exists in code but Razorpay never actually calls it.
    Payload shapes below are taken directly from Razorpay's own published
    webhook documentation (docs.razorpay.com/webhooks/payloads/subscriptions),
    not guessed — unlike the WhatsApp interactive-button case earlier in
    this project, these are well-documented and don't need a live-tap
    confirmation before trusting the field paths.
    """
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not razorpay_client.verify_webhook_signature(raw_body.decode(), signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    payload = await request.json()
    event = payload.get("event")
    db = SessionLocal()
    try:
        if event == "subscription.charged":
            await _handle_charged(db, payload)
        elif event in ("subscription.cancelled", "subscription.halted", "subscription.completed"):
            _handle_ended(db, payload)
        else:
            logger.info("Ignoring unhandled Razorpay webhook event: %s", event)
    finally:
        db.close()
    return {"received": True}


async def _handle_charged(db: Session, payload: dict) -> None:
    """
    A successful renewal charge — extends subscription_expires_at by a
    fresh term and logs a real ledger entry (external_ref = this payment's
    id) exactly like the very first payment did, so renewals show up in
    reconciliation/statements too, not just the initial signup.
    """
    subscription_entity = payload["payload"]["subscription"]["entity"]
    payment_entity = payload["payload"]["payment"]["entity"]
    subscription_id = subscription_entity["id"]
    payment_id = payment_entity["id"]

    student = db.query(Student).filter(Student.razorpay_subscription_id == subscription_id).first()
    if student is None:
        logger.warning("Razorpay subscription.charged for unknown subscription_id=%s", subscription_id)
        return
    if cost_tracker.has_processed_external_ref(db, payment_id):
        return  # webhook retry — Razorpay redelivers on anything but a 2xx response, safe to no-op

    days = (
        cost_tracker.UNLIMITED_TEACHER_MONTHLY_DAYS if student.is_staff_profile
        else cost_tracker.UNLIMITED_STUDENT_ANNUAL_DAYS
    )
    price = payment_entity["amount"] / 100
    student.subscription_plan = "unlimited"
    student.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    cost_tracker.add_credits(
        db, student.id, price, note="Razorpay subscription renewal",
        external_ref=payment_id, service="unlimited_plan_recurring",
    )
    db.commit()
    await send_whatsapp_message(
        student.phone,
        f"✅ Your Qlass unlimited plan renewed successfully! Active until "
        f"{student.subscription_expires_at.strftime('%d %b %Y')}.",
    )


def _handle_ended(db: Session, payload: dict) -> None:
    """
    subscription.cancelled (explicit cancellation), subscription.halted
    (Razorpay exhausted its own retry attempts after repeated charge
    failures), or subscription.completed (hit total_count — see
    razorpay_client.STUDENT_SUBSCRIPTION_TOTAL_CYCLES/TEACHER_..., an
    intentionally long stand-in for "until cancelled" rather than a real
    end date, so this is expected to be rare in practice). None of these
    force early revocation — the student/teacher keeps their access until
    subscription_expires_at (the period already paid for), same reasoning
    as the self-serve /cancel endpoints — this just stops it from
    auto-renewing any further.
    """
    subscription_entity = payload["payload"]["subscription"]["entity"]
    subscription_id = subscription_entity["id"]
    student = db.query(Student).filter(Student.razorpay_subscription_id == subscription_id).first()
    if student is None:
        return
    student.razorpay_subscription_id = None
    db.commit()
