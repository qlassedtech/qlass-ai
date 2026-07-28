from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import CreditEvent

# Every deduction is charged at (actual provider cost) x MARKUP_MULTIPLIER —
# covers overhead/margin rather than passing through raw provider cost 1:1.
MARKUP_MULTIPLIER = 2.0

# All figures in INR. Sarvam rates below are taken directly from a real
# Sarvam usage statement (Mayura v1 / Bulbul v3 / Saaras v3); Claude and
# Azure rates are rough placeholders converted from published USD list
# prices — update these as real invoices confirm exact figures.
PRICING = {
    "claude_sonnet": {"input_per_1k_tokens": 0.26, "output_per_1k_tokens": 1.31},  # ~$3 / $15 per 1M tokens
    "claude_haiku": {"input_per_1k_tokens": 0.07, "output_per_1k_tokens": 0.35},  # ~$0.80 / $4 per 1M tokens
    "sarvam_tts": {"per_char": 0.00301},  # Bulbul v3: ₹34.31 / 11,400 chars
    "sarvam_stt": {"per_minute": 0.503},  # Saaras v3: ₹1.46 / 2.9 min
    "sarvam_translate": {"per_char": 0.00200},  # Mayura v1: ₹70.06 / 35,000 chars
    "azure_ocr": {"per_call": 0.09},  # ~$1 per 1,000 transactions (S1 Read API tier)
    "azure_image": {"per_call": 3.0},  # rough per-image estimate, gpt-image-1-mini
}


def _tier_for_model(model: str) -> str:
    return "claude_haiku" if "haiku" in model else "claude_sonnet"


def get_balance(db: Session) -> float:
    total = db.query(func.coalesce(func.sum(CreditEvent.amount), 0)).scalar()
    return float(total)


def has_credits(db: Session) -> bool:
    return get_balance(db) > 0


def add_credits(db: Session, amount: float, note: str | None = None) -> float:
    db.add(CreditEvent(amount=amount, note=note))
    db.commit()
    return get_balance(db)


def _deduct(db: Session, service: str, raw_cost: float, student_id: int | None) -> float:
    if raw_cost <= 0:
        return get_balance(db)  # nothing actually billed (e.g. a failed/no-op call) — no ledger noise
    db.add(CreditEvent(amount=-raw_cost * MARKUP_MULTIPLIER, service=service, raw_cost=raw_cost, student_id=student_id))
    db.commit()
    return get_balance(db)


def record_claude_usage(db: Session, model: str, input_tokens: int, output_tokens: int, student_id: int | None = None) -> float:
    tier = _tier_for_model(model)
    rates = PRICING[tier]
    raw_cost = (input_tokens / 1000) * rates["input_per_1k_tokens"] + (output_tokens / 1000) * rates["output_per_1k_tokens"]
    return _deduct(db, tier, raw_cost, student_id)


def record_char_usage(db: Session, service: str, char_count: int, student_id: int | None = None) -> float:
    raw_cost = char_count * PRICING[service]["per_char"]
    return _deduct(db, service, raw_cost, student_id)


def record_minute_usage(db: Session, service: str, minutes: float, student_id: int | None = None) -> float:
    raw_cost = minutes * PRICING[service]["per_minute"]
    return _deduct(db, service, raw_cost, student_id)


def record_flat_usage(db: Session, service: str, student_id: int | None = None) -> float:
    raw_cost = PRICING[service]["per_call"]
    return _deduct(db, service, raw_cost, student_id)
