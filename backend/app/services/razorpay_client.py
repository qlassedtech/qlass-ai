import razorpay

from app.config import settings

MIN_TOPUP_AMOUNT = 10.0  # INR — small enough to be accessible, large enough that Razorpay's own fee doesn't dominate it

client = (
    razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    if settings.razorpay_key_id and settings.razorpay_key_secret
    else None
)

# Razorpay Subscriptions require a fixed total_count of billing cycles —
# there's no literal "forever" option. 10 years' worth of cycles is a
# practical stand-in for "until cancelled": comfortably longer than any
# real subscriber is likely to keep auto-renewing without us revisiting
# pricing anyway, and the student/teacher can cancel at any time before
# reaching it (see cancel_subscription).
STUDENT_SUBSCRIPTION_TOTAL_CYCLES = 10  # years
TEACHER_SUBSCRIPTION_TOTAL_CYCLES = 120  # months


def create_subscription(plan_id: str, total_count: int, notes: dict) -> dict:
    return client.subscription.create({
        "plan_id": plan_id,
        "customer_notify": 1,
        "total_count": total_count,
        "notes": notes,
    })


def fetch_subscription(subscription_id: str) -> dict:
    return client.subscription.fetch(subscription_id)


def cancel_subscription(subscription_id: str) -> dict:
    return client.subscription.cancel(subscription_id)


def verify_webhook_signature(payload_body: str, signature: str) -> bool:
    if not settings.razorpay_webhook_secret:
        return False
    try:
        client.utility.verify_webhook_signature(payload_body, signature, settings.razorpay_webhook_secret)
        return True
    except razorpay.errors.SignatureVerificationError:
        return False


def require_paid_order(order: dict, payment: dict, expected_notes: dict[str, str]) -> None:
    """Validate the server-side Razorpay records before granting a top-up.

    Signature verification proves that a payment belongs to an order.  It
    does *not* prove that the caller is entitled to apply that order to the
    local account supplied in the request, so the order notes are the
    binding between Razorpay and our tenant/student record.
    """
    if order.get("status") != "paid" or order.get("currency") != "INR":
        raise ValueError("Order is not a paid INR order")
    if payment.get("status") != "captured" or payment.get("order_id") != order.get("id"):
        raise ValueError("Payment is not a captured payment for this order")
    notes = order.get("notes") or {}
    if any(str(notes.get(key)) != str(value) for key, value in expected_notes.items()):
        raise ValueError("Order does not belong to this account")


def require_active_subscription(
    subscription: dict, payment: dict, expected_notes: dict[str, str], expected_plan_id: str
) -> None:
    """Validate ownership, plan, and captured first payment for a subscription."""
    if subscription.get("status") not in {"active", "authenticated"}:
        raise ValueError("Subscription is not active")
    if subscription.get("plan_id") != expected_plan_id:
        raise ValueError("Subscription uses an unexpected plan")
    if payment.get("status") != "captured" or payment.get("subscription_id") != subscription.get("id"):
        raise ValueError("Payment is not a captured payment for this subscription")
    notes = subscription.get("notes") or {}
    if any(str(notes.get(key)) != str(value) for key, value in expected_notes.items()):
        raise ValueError("Subscription does not belong to this account")
