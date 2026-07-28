from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import CreditEvent

# Every deduction is charged at (actual provider cost) x MARKUP_MULTIPLIER —
# covers overhead/margin rather than passing through raw provider cost 1:1.
MARKUP_MULTIPLIER = 2.0

# Anthropic prompt-caching multipliers on the base input rate: writing to
# the cache costs MORE than a normal read (1.25x) since Anthropic has to do
# the caching work, but a cache HIT costs a tenth of a normal read (0.1x) —
# the whole point of marking the tutor's system prompt as cacheable.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.1

# YouTube Data API v3 search is free up to this many calls/day (10,000 daily
# quota units ÷ 100 units per search) — Google doesn't meter or bill past
# this itself, it just returns quotaExceeded. See record_youtube_search.
YOUTUBE_FREE_SEARCHES_PER_DAY = 100

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
    # YouTube Data API v3 has no public per-call overage price (past its
    # free daily quota, calls just fail with quotaExceeded rather than being
    # billed) — this rate is a deliberate stand-in, benchmarked against
    # Google's own Custom Search JSON API, which has an identical "100
    # free/day" structure and a real published overage price of $5/1,000
    # queries (~₹0.44/query at ~₹87/USD). Update if you get an actual quota
    # increase / billing arrangement from Google for YouTube specifically.
    "youtube_search_overage": {"per_call": 0.44},
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


def record_claude_usage(
    db: Session,
    model: str,
    input_tokens: int,
    output_tokens: int,
    student_id: int | None = None,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    tier = _tier_for_model(model)
    rates = PRICING[tier]
    raw_cost = (
        (input_tokens / 1000) * rates["input_per_1k_tokens"]
        + (output_tokens / 1000) * rates["output_per_1k_tokens"]
        + (cache_write_tokens / 1000) * rates["input_per_1k_tokens"] * CACHE_WRITE_MULTIPLIER
        + (cache_read_tokens / 1000) * rates["input_per_1k_tokens"] * CACHE_READ_MULTIPLIER
    )
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


def record_free_call(db: Session, service: str, student_id: int | None = None) -> None:
    """
    Log a $0 audit entry for a call that isn't billed under normal usage —
    not a deduction, just visibility into how many calls have happened.
    """
    db.add(CreditEvent(amount=0, service=service, raw_cost=0, student_id=student_id, note="free tier — not billed"))
    db.commit()


def _period_start(period: str) -> datetime:
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return today_start
    if period == "week":
        return today_start - timedelta(days=today_start.weekday())  # most recent Monday 00:00 UTC
    if period == "month":
        return today_start.replace(day=1)
    raise ValueError(f"unknown period: {period!r}")


def get_student_monthly_spend(db: Session, student_id: int) -> float:
    """
    Actual ₹ this student has been charged (at the already-2x-marked-up
    ledger rate) since the start of the current calendar month — the basis
    for the ₹100/month per-student credit limit. Only counts deductions
    (negative amounts), not top-ups, so a manual credit adjustment for one
    student doesn't get miscounted as "spend."
    """
    total = (
        db.query(func.coalesce(func.sum(-CreditEvent.amount), 0))
        .filter(
            CreditEvent.student_id == student_id,
            CreditEvent.amount < 0,
            CreditEvent.created_at >= _period_start("month"),
        )
        .scalar()
    )
    return float(total)


def get_usage_count(db: Session, student_id: int, services: list[str], period: str) -> int:
    """
    How many times this student has used any of `services` since the start
    of the current calendar day/week (UTC) — the basis for daily/weekly
    per-feature usage caps. Reuses the existing credit_events ledger rather
    than a separate counter table, since every billable (or free-tier
    tracked) call already writes a row there tagged by service.
    """
    return (
        db.query(func.count(CreditEvent.id))
        .filter(
            CreditEvent.student_id == student_id,
            CreditEvent.service.in_(services),
            CreditEvent.created_at >= _period_start(period),
        )
        .scalar()
    )


def get_usage_counts_by_service(db: Session, student_id: int, services: list[str], period: str) -> dict[str, int]:
    """
    Same idea as get_usage_count, but for several services at once in a
    single query (GROUP BY) instead of one round-trip per service — used to
    check voice/image/video caps together instead of up to three separate
    queries per turn.
    """
    rows = (
        db.query(CreditEvent.service, func.count(CreditEvent.id))
        .filter(
            CreditEvent.student_id == student_id,
            CreditEvent.service.in_(services),
            CreditEvent.created_at >= _period_start(period),
        )
        .group_by(CreditEvent.service)
        .all()
    )
    return {service: count for service, count in rows}


def record_youtube_search(db: Session, student_id: int | None = None) -> float:
    """
    Free for the first YOUTUBE_FREE_SEARCHES_PER_DAY searches each calendar
    day (matching Google's actual daily quota), then charged at the
    youtube_search_overage rate x MARKUP_MULTIPLIER — same "actual cost x2"
    policy as every other service, applied from the point real cost would
    realistically start.
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = (
        db.query(func.count(CreditEvent.id))
        .filter(CreditEvent.service.in_(["youtube_search", "youtube_search_overage"]), CreditEvent.created_at >= today_start)
        .scalar()
    )
    if today_count < YOUTUBE_FREE_SEARCHES_PER_DAY:
        record_free_call(db, "youtube_search", student_id)
        return get_balance(db)
    return record_flat_usage(db, "youtube_search_overage", student_id)
