from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import SchoolCreditEvent

# Same "actual provider cost x markup" policy as the per-student ledger
# (see app.services.cost_tracker), applied to teacher-facing generation
# tools that are billed to the school rather than any one student.
MARKUP_MULTIPLIER = 2.0

# Every new school starts with this much credit for teacher tools (workbook
# PDFs, presentations) — separate pool from student trial credits, covers a
# school trying these out before ever topping up.
SCHOOL_TRIAL_CREDITS = 50.0

PRICING = {
    # Reuses the same Claude Sonnet rates as cost_tracker.PRICING — kept
    # duplicated rather than imported to avoid coupling the two ledgers'
    # pricing tables together (they're allowed to diverge over time).
    "workbook_pdf": {"input_per_1k_tokens": 0.26, "output_per_1k_tokens": 1.31},
}

# Confirmed from the real Qlass Gamma account: Pro plan, ₹1300/month for
# 4,000 credits. Real cost varies a lot per generation (slide count, image
# options) — a 3-slide test deck cost 13 credits (₹4.23) — so Gamma usage
# is billed from the ACTUAL credits Gamma reports per generation (see
# record_gamma_usage) rather than a flat per-call guess.
GAMMA_CREDIT_INR = 1300 / 4000
GAMMA_PRESENTATION_SERVICE = "gamma_presentation"


def get_balance(db: Session, centre_id: int) -> float:
    total = (
        db.query(func.coalesce(func.sum(SchoolCreditEvent.amount), 0))
        .filter(SchoolCreditEvent.centre_id == centre_id)
        .scalar()
    )
    return float(total)


def has_credits(db: Session, centre_id: int) -> bool:
    return get_balance(db, centre_id) > 0


def add_credits(
    db: Session, centre_id: int, amount: float, note: str | None = None, external_ref: str | None = None,
    service: str | None = None,
) -> float:
    db.add(SchoolCreditEvent(amount=amount, service=service, centre_id=centre_id, note=note, external_ref=external_ref))
    db.commit()
    return get_balance(db, centre_id)


def has_processed_external_ref(db: Session, external_ref: str) -> bool:
    """Same idempotency purpose as cost_tracker.has_processed_external_ref."""
    return db.query(SchoolCreditEvent).filter(SchoolCreditEvent.external_ref == external_ref).first() is not None


def add_trial_credits(db: Session, centre_id: int) -> float:
    return add_credits(db, centre_id, SCHOOL_TRIAL_CREDITS, note="Qlass trial credit")


def record_claude_usage(
    db: Session, centre_id: int, service: str, input_tokens: int, output_tokens: int
) -> float:
    rates = PRICING[service]
    raw_cost = (input_tokens / 1000) * rates["input_per_1k_tokens"] + (output_tokens / 1000) * rates["output_per_1k_tokens"]
    if raw_cost <= 0:
        return get_balance(db, centre_id)
    db.add(SchoolCreditEvent(
        amount=-raw_cost * MARKUP_MULTIPLIER, service=service, raw_cost=raw_cost, centre_id=centre_id,
    ))
    db.commit()
    return get_balance(db, centre_id)


def has_billed_gamma_generation(db: Session, generation_id: str) -> bool:
    """
    The frontend polls /admin/presentation/status repeatedly until a
    generation completes — this guards against billing the same
    generation more than once across those repeated polls.
    """
    return (
        db.query(SchoolCreditEvent)
        .filter(SchoolCreditEvent.service == GAMMA_PRESENTATION_SERVICE, SchoolCreditEvent.note.like(f"%{generation_id}%"))
        .first()
        is not None
    )


def record_gamma_usage(db: Session, centre_id: int, generation_id: str, credits_deducted: int) -> float:
    raw_cost = credits_deducted * GAMMA_CREDIT_INR
    db.add(SchoolCreditEvent(
        amount=-raw_cost * MARKUP_MULTIPLIER, service=GAMMA_PRESENTATION_SERVICE, raw_cost=raw_cost,
        centre_id=centre_id, note=f"Gamma generation {generation_id} ({credits_deducted} credits)",
    ))
    db.commit()
    return get_balance(db, centre_id)
