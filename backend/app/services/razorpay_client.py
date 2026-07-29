import razorpay

from app.config import settings

MIN_TOPUP_AMOUNT = 10.0  # INR — small enough to be accessible, large enough that Razorpay's own fee doesn't dominate it

client = (
    razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    if settings.razorpay_key_id and settings.razorpay_key_secret
    else None
)
