from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.business_rules import (
    MARKUP_MULTIPLIER,
    TRIAL_CREDITS,
    UNLIMITED_PERIOD_SPEND_CAPS,
    UNLIMITED_STUDENT_ANNUAL_DAYS,
    UNLIMITED_STUDENT_ANNUAL_PRICE,
    UNLIMITED_TEACHER_MONTHLY_DAYS,
    UNLIMITED_TEACHER_MONTHLY_PRICE,
    WALLET_USAGE_WARNING_FRACTIONS,
)
from app.models.core import CreditEvent, Student

# UNLIMITED_STUDENT_ANNUAL_DAYS/PRICE and UNLIMITED_TEACHER_MONTHLY_DAYS/
# PRICE aren't used inside this file, only re-exported so existing callers
# (payments.py, admin.py, razorpay_webhook.py) keep working unchanged as
# cost_tracker.X — listed here so pyflakes doesn't flag them as unused.
__all__ = [
    "UNLIMITED_STUDENT_ANNUAL_DAYS", "UNLIMITED_STUDENT_ANNUAL_PRICE",
    "UNLIMITED_TEACHER_MONTHLY_DAYS", "UNLIMITED_TEACHER_MONTHLY_PRICE",
]

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
    # Tutor levels 1-2 (see app.business_rules.TUTOR_LEVEL_MODELS) — rates
    # confirmed live against official pricing pages at ~₹87/USD, 2026-08-11.
    "gpt4o_mini": {"input_per_1k_tokens": 0.01305, "output_per_1k_tokens": 0.0522},  # $0.15 / $0.60 per 1M tokens
    "gemini_flash_lite": {"input_per_1k_tokens": 0.02175, "output_per_1k_tokens": 0.1305},  # $0.25 / $1.50 per 1M tokens
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
    if "haiku" in model:
        return "claude_haiku"
    if "gemini" in model:
        return "gemini_flash_lite"
    if "gpt-4o-mini" in model:
        return "gpt4o_mini"
    return "claude_sonnet"


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


def get_credit_history(db: Session, student_id: int, limit: int = 50) -> list[CreditEvent]:
    """
    Recent-first raw ledger rows for a student's own "transaction history"
    view — distinct from get_balance (a single aggregate number) and from
    every admin-side usage-cap query, which only ever need sums/counts, not
    the individual rows themselves.
    """
    return (
        db.query(CreditEvent)
        .filter(CreditEvent.student_id == student_id)
        .order_by(CreditEvent.created_at.desc())
        .limit(limit)
        .all()
    )


def _is_unlimited_active(student: Student) -> bool:
    """
    True while a student is on the flat-fee unlimited plan (₹2499/yr for a
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


def get_student_period_cost_equivalent(db: Session, student_id: int, period: str) -> float:
    """
    This day/week/month's usage translated into the same ₹ terms a
    pay-as-you-go student would have been billed (raw_cost ×
    MARKUP_MULTIPLIER) — the basis for the unlimited-plan period caps
    below. Computed from raw_cost rather than `amount` because an unlimited
    student's `amount` is usually 0 while covered by their flat fee (see
    _deduct), so it can't be summed directly the way
    get_student_monthly_spend does for wallet students.
    """
    total = (
        db.query(func.coalesce(func.sum(CreditEvent.raw_cost), 0))
        .filter(CreditEvent.student_id == student_id, CreditEvent.created_at >= _period_start(period))
        .scalar()
    )
    return float(total) * MARKUP_MULTIPLIER


def get_unlimited_usage_status(db: Session, student: Student) -> dict[str, dict[str, float]]:
    role = "teacher" if student.is_staff_profile else "student"
    caps = UNLIMITED_PERIOD_SPEND_CAPS[role]
    return {
        period: {"spent": get_student_period_cost_equivalent(db, student.id, period), "cap": cap}
        for period, cap in caps.items()
    }


def is_unlimited_over_period_cap(db: Session, student: Student) -> bool:
    return any(v["spent"] >= v["cap"] for v in get_unlimited_usage_status(db, student).values())


def get_last_recharge(db: Session, student_id: int) -> CreditEvent | None:
    """
    Most recent balance-increasing event that isn't a small milestone bonus
    (referral/habit) — the reference "pack" a pay-as-you-go student's
    low-balance reminder is measured against, mirroring the prepaid-mobile-
    recharge mental model most students/parents already know (e.g. "you've
    used 50% of your last recharge"). A wallet has no fixed monthly reset to
    measure against once topped up, so this is the closest real equivalent.
    """
    return (
        db.query(CreditEvent)
        .filter(
            CreditEvent.student_id == student_id, CreditEvent.amount > 0,
            # NULL-safe: a plain top-up/trial grant has no `service` at all
            # (see add_trial_credits), and SQL's "NULL NOT IN (...)" is
            # UNKNOWN rather than TRUE — a bare .notin_() would silently
            # exclude those rows too, not just referral/habit bonuses.
            or_(
                CreditEvent.service.is_(None),
                CreditEvent.service.notin_([REFERRAL_BONUS_SERVICE, HABIT_BONUS_SERVICE]),
            ),
        )
        .order_by(CreditEvent.created_at.desc())
        .first()
    )


def get_spend_since(db: Session, student_id: int, since: datetime) -> float:
    total = (
        db.query(func.coalesce(func.sum(-CreditEvent.amount), 0))
        .filter(CreditEvent.student_id == student_id, CreditEvent.amount < 0, CreditEvent.created_at >= since)
        .scalar()
    )
    return float(total)


def has_credits(db: Session, student_id: int) -> bool:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is not None and _is_unlimited_active(student):
        # Still within their flat-fee allotment for this day/week/month —
        # unmetered, same as always.
        if not is_unlimited_over_period_cap(db, student):
            return True
        # Over the allotment — from here on they're spending real "usage
        # credits" (the same wallet a pay-as-you-go student uses, just
        # normally untouched for an unlimited-plan student — see _deduct),
        # so the gate becomes the same wallet-balance check everyone else
        # gets, not an outright block until the period resets.
        return get_balance(db, student_id) > 0
    return get_balance(db, student_id) > 0


def get_usage_fraction(db: Session, student: Student) -> float | None:
    """
    How "used up" this student's current usage cap is, as a fraction — the
    basis for a 50/75/90% heads-up reminder before they actually run out.
    None means there's nothing meaningful to warn about (e.g. no recharge
    event yet to measure against). Shared across every surface (WhatsApp,
    student web/app, teacher's My AI Tutor) since it's just billing state,
    not channel-specific — see app.routers.whatsapp._current_usage_fraction
    for the one channel-specific wrinkle (skipping FULL_ACCESS_PHONES).

    Two different meanings depending on plan, since they're billed
    completely differently:
    - Unlimited-plan student: the most-consumed of their day/week/month
      flat-fee allotment (see get_unlimited_usage_status) — whichever
      period is closest to pushing them into spending real "usage credits"
      (see _deduct) is the one worth warning about.
    - Pay-as-you-go student: how much of their most recent recharge (trial
      grant or top-up) they've spent through — there's no fixed monthly
      reset to measure against once a wallet's been topped up more than
      once, so the last recharge is the closest real equivalent to "how
      much of what you paid for is left" (see get_last_recharge).
    """
    if is_unlimited_active(student):
        status = get_unlimited_usage_status(db, student)
        fractions = [v["spent"] / v["cap"] for v in status.values() if v["cap"] > 0]
        return max(fractions) if fractions else None
    recharge = get_last_recharge(db, student.id)
    if recharge is None or recharge.amount <= 0:
        return None
    spent = get_spend_since(db, student.id, recharge.created_at)
    return spent / float(recharge.amount)


def usage_threshold_notice(fraction_before: float | None, fraction_after: float | None) -> str | None:
    """
    A fraction is continuous, not an integer count, so it won't land
    exactly on a 50%/75%/90% boundary — instead, check whether this turn's
    cost carried it *across* a threshold, and report the highest one
    crossed (covers an expensive single turn, e.g. voice, jumping past more
    than one threshold at once, so only one notice goes out per turn). Never
    fires at/past 100% — running out is its own, more prominent notice with
    real next-step buttons/payment options, so a plain text reminder here
    would just be a weaker duplicate of that.
    """
    if fraction_after is None:
        return None
    before = fraction_before if fraction_before is not None else 0.0
    crossed = [f for f in WALLET_USAGE_WARNING_FRACTIONS if before < f <= fraction_after]
    if not crossed:
        return None
    fraction = max(crossed)
    return f"ℹ️ Heads up — you've used {round(fraction * 100)}% of your available AI credits for this period."


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
    # Unlimited-plan students (flat ₹2499/yr or ₹3500/mo) have their wallet
    # drawn down (amount=0 below) only once they've burned through their
    # day/week/month allotment (see UNLIMITED_PERIOD_RAW_COST_CAPS) — until
    # then this is real COGS Qlass can check against the flat fee later,
    # even though amount alone shows zero spend for these students. Past
    # the allotment, further usage draws from the same wallet a normal
    # pay-as-you-go student uses (see has_credits) — i.e. bought "usage
    # credits" — rather than being blocked outright until the period rolls
    # over, which would just push the student out of the habit loop.
    covered_by_flat_fee = (
        student is not None and _is_unlimited_active(student) and not is_unlimited_over_period_cap(db, student)
    )
    amount = 0.0 if covered_by_flat_fee else -raw_cost * MARKUP_MULTIPLIER
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
