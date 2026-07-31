from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import CreditEvent, Student

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
# this itself, it just returns quotaExceeded. This quota is per Qlass API
# key (i.e. account-wide), NOT per student, so it's deliberately tracked
# globally rather than per-wallet — see record_youtube_search.
YOUTUBE_FREE_SEARCHES_PER_DAY = 100

# Every new student wallet starts with this much free trial credit — Qlass
# covers it (not a real charge to anyone), so a school can try the product
# before a parent/student ever has to pay. See add_trial_credits. Demo/
# testing numbers (FULL_ACCESS_PHONES in whatsapp.py) get unlimited credits
# instead, bypassing this wallet check entirely.
TRIAL_CREDITS = 50.0

# Flat-fee unlimited plan pricing (see _is_unlimited_active) — a real
# student's ₹1800/yr, and a teacher's own "My AI Tutor" profile's ₹3500/mo.
# Used both to price a manual super_admin activation (see admin.py's
# set_student_subscription/activate_teacher_tutor_subscription) and to
# prorate it if a shorter/longer duration is granted than the standard term.
UNLIMITED_STUDENT_ANNUAL_PRICE = 1800.0  # for the standard 365-day term
UNLIMITED_STUDENT_ANNUAL_DAYS = 365
UNLIMITED_TEACHER_MONTHLY_PRICE = 3500.0  # for the standard 30-day term
UNLIMITED_TEACHER_MONTHLY_DAYS = 30

# Referral credits are milestone-based (signup + day1-3 activity + week2/3
# activity — see app.services.referral) and deliberately uncapped: Qlass
# wants to maximize referral volume, so a student who refers many friends
# can keep earning across every one of them.
REFERRAL_BONUS_SERVICE = "referral_bonus"

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


def get_balance(db: Session, student_id: int) -> float:
    """
    Each student has their own wallet — this sums only that student's rows
    in the shared credit_events ledger (student_id is the partition key,
    not a separate table, so the existing audit trail/usage-cap queries
    keep working unchanged).
    """
    total = (
        db.query(func.coalesce(func.sum(CreditEvent.amount), 0))
        .filter(CreditEvent.student_id == student_id)
        .scalar()
    )
    return float(total)


def _is_unlimited_active(student: Student) -> bool:
    """
    True while a student is on the flat-fee unlimited plan (₹1800/yr for a
    real student, ₹3500/mo for a teacher's own "My AI Tutor" profile — same
    two columns serve both, since a teacher's personal profile is just a
    Student row with is_staff_profile=True) and that plan hasn't expired.
    """
    if student.subscription_plan != "unlimited" or student.subscription_expires_at is None:
        return False
    expires_at = student.subscription_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


# Public alias — routers outside this module (e.g. payments.py's
# subscription-creation endpoint) need this check too, without reaching
# into a name-mangled "private" function.
is_unlimited_active = _is_unlimited_active


def has_credits(db: Session, student_id: int) -> bool:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is not None and _is_unlimited_active(student):
        return True
    return get_balance(db, student_id) > 0


def add_credits(
    db: Session, student_id: int, amount: float, note: str | None = None, external_ref: str | None = None,
    service: str | None = None,
) -> float:
    db.add(CreditEvent(amount=amount, service=service, student_id=student_id, note=note, external_ref=external_ref))
    db.commit()
    return get_balance(db, student_id)


def has_processed_external_ref(db: Session, external_ref: str) -> bool:
    """
    True if a payment with this external reference (e.g. a Razorpay
    payment_id) has already been credited — guards /pay/verify against
    double-crediting the same payment on a client retry or replay.
    """
    return db.query(CreditEvent).filter(CreditEvent.external_ref == external_ref).first() is not None


def has_independent_payment(db: Session, student_id: int) -> bool:
    """
    True if this student has ever paid Qlass directly for AI credits (a
    real Razorpay payment via /pay/verify, which is the only code path
    that ever sets external_ref) — as opposed to only ever having received
    school-funded trial credit, referral/habit bonuses, or a manual admin
    grant, none of which set external_ref. Used to decide whether a
    student at a "churned" school should keep getting service on their own
    dime (see app.services.school_billing.is_centre_churned).
    """
    return db.query(CreditEvent).filter(CreditEvent.student_id == student_id, CreditEvent.external_ref.isnot(None)).first() is not None


def add_trial_credits(db: Session, student_id: int) -> float:
    """Called once when a student's wallet is created — see TRIAL_CREDITS."""
    return add_credits(db, student_id, TRIAL_CREDITS, note="Qlass trial credit")


def get_referral_credits_earned(db: Session, student_id: int) -> float:
    total = (
        db.query(func.coalesce(func.sum(CreditEvent.amount), 0))
        .filter(CreditEvent.student_id == student_id, CreditEvent.service == REFERRAL_BONUS_SERVICE)
        .scalar()
    )
    return float(total)


def grant_referral_credit(db: Session, referrer_student_id: int, amount: float, note: str) -> float:
    """
    Pays a referral milestone bonus — uncapped by design (see
    REFERRAL_BONUS_SERVICE above), so a student who refers many friends
    keeps earning across every one of them. See app.services.referral for
    the actual milestone schedule (signup / day1-3 activity / week2-3
    activity) that decides when and how much to grant.
    """
    db.add(CreditEvent(amount=amount, service=REFERRAL_BONUS_SERVICE, student_id=referrer_student_id, note=note))
    db.commit()
    return amount


HABIT_BONUS_SERVICE = "habit_bonus"


def grant_habit_credit(db: Session, student_id: int, amount: float, note: str) -> float:
    """Pays a 21-day habit-building milestone bonus — see app.services.habit."""
    db.add(CreditEvent(amount=amount, service=HABIT_BONUS_SERVICE, student_id=student_id, note=note))
    db.commit()
    return amount


def _deduct(db: Session, service: str, raw_cost: float, student_id: int) -> float:
    if raw_cost <= 0:
        return get_balance(db, student_id)  # nothing actually billed (e.g. a failed/no-op call) — no ledger noise
    student = db.query(Student).filter(Student.id == student_id).first()
    # Unlimited-plan students (flat ₹1800/yr or ₹3500/mo) never have their
    # wallet drawn down (amount=0 below) — but raw_cost is always the real,
    # un-marked-up provider cost regardless of plan, so SUM(raw_cost) per
    # student is real COGS Qlass can check against the flat fee later, even
    # though amount alone would show zero spend for these students.
    amount = 0.0 if (student is not None and _is_unlimited_active(student)) else -raw_cost * MARKUP_MULTIPLIER
    db.add(CreditEvent(amount=amount, service=service, raw_cost=raw_cost, student_id=student_id))
    db.commit()
    return get_balance(db, student_id)


def record_claude_usage(
    db: Session,
    model: str,
    input_tokens: int,
    output_tokens: int,
    student_id: int,
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


def record_char_usage(db: Session, service: str, char_count: int, student_id: int) -> float:
    raw_cost = char_count * PRICING[service]["per_char"]
    return _deduct(db, service, raw_cost, student_id)


def record_minute_usage(db: Session, service: str, minutes: float, student_id: int) -> float:
    raw_cost = minutes * PRICING[service]["per_minute"]
    return _deduct(db, service, raw_cost, student_id)


def record_flat_usage(db: Session, service: str, student_id: int) -> float:
    raw_cost = PRICING[service]["per_call"]
    return _deduct(db, service, raw_cost, student_id)


def record_free_call(db: Session, service: str, student_id: int) -> None:
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


def record_youtube_search(db: Session, student_id: int) -> float:
    """
    Free for the first YOUTUBE_FREE_SEARCHES_PER_DAY searches each calendar
    day account-wide (matching Google's actual per-API-key daily quota —
    NOT per-student, since the quota is shared across every student on this
    Qlass account), then charged at the youtube_search_overage rate x
    MARKUP_MULTIPLIER to whichever student's turn pushed it over, same
    "actual cost x2" policy as every other service.
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = (
        db.query(func.count(CreditEvent.id))
        .filter(CreditEvent.service.in_(["youtube_search", "youtube_search_overage"]), CreditEvent.created_at >= today_start)
        .scalar()
    )
    if today_count < YOUTUBE_FREE_SEARCHES_PER_DAY:
        record_free_call(db, "youtube_search", student_id)
        return get_balance(db, student_id)
    return record_flat_usage(db, "youtube_search_overage", student_id)
