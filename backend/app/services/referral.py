import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.core import ChatHistory, CreditEvent, Student
from app.services import cost_tracker
from app.services.whatsapp_client import send_whatsapp_message

REFERRAL_CODE_PREFIX = "QL"
_CODE_PATTERN = re.compile(r"\bQL(\d{4,})\b")

# How long after signup a student can still link a referral code sent in a
# LATER message (not just their very first one). Confirmed live: a new
# student who greets first ("Hello") and pastes their friend's code as a
# separate second message never got credited, since the code was only ever
# read from the single first inbound message at account-creation time (see
# whatsapp.py's _create_new_student). Bounded to a short window, rather than
# allowed at any time, so an already-engaged student can't retroactively
# attach a referral days/weeks later just to hand a referrer a "signup"
# bonus for an account that was never really driven by that referral.
LATE_REFERRAL_CLAIM_WINDOW = timedelta(hours=24)

# Paid the moment someone signs up mentioning a referral code — see
# whatsapp.py's _create_new_student. Not gated on any activity.
REFERRAL_SIGNUP_BONUS = 10.0

_SIGNUP_BONUS_NOTE = "Referral milestone: signup"

# Confirmed live (code review, Aug 2026): being unconditional makes the
# signup bonus the one milestone that doesn't require the referred student
# to actually do anything, which is exactly what makes it farmable —
# combined with new-account creation having no rate limit either (see
# app.services.rate_limit.is_signup_rate_limited, fixed alongside this),
# one operator with many throwaway numbers could otherwise collect an
# unbounded stream of ₹10 payouts to a single "real" account. This doesn't
# touch the day1/2/3/week2/3 milestones below — those already require the
# referred student to have sent real messages, which is a much higher bar
# than owning a phone number.
MAX_SIGNUP_BONUSES_PER_REFERRER_PER_DAY = 5

# (milestone name, day-window start, day-window end [exclusive], minimum
# questions the referred student must have asked somewhere in that window,
# bonus paid to the referrer). Evaluated live on every message the referred
# student sends — see evaluate_referral_milestones, called from whatsapp.py.
# Windows are elapsed days since the referred student's own signup.
REFERRAL_MILESTONES: list[tuple[str, int, int, int, float]] = [
    ("day1", 1, 2, 2, 10.0),
    ("day2", 2, 3, 2, 10.0),
    ("day3", 3, 4, 2, 10.0),
    ("week2", 7, 14, 1, 20.0),
    ("week3", 14, 20, 1, 30.0),
]

# The most a referrer can ever earn from a single referral: the signup
# bonus plus every milestone bonus, if the referred student hits all of
# them. Shown to a student asking about the referral program (see
# app.services.chat_core's "referral" intent) so the number quoted always
# matches REFERRAL_MILESTONES instead of drifting out of sync with it.
REFERRAL_LIFETIME_CAP = REFERRAL_SIGNUP_BONUS + sum(bonus for _, _, _, _, bonus in REFERRAL_MILESTONES)

# "Worth asking to refer a friend" bar — shared between the WhatsApp main
# menu (see whatsapp.py's MENU_BUTTONS) and scripts/send_referral_nudges.py,
# so both use the same definition of a credible, high-conversion referrer
# rather than two independently-tuned thresholds. Asking a brand-new
# student to vouch for a product they haven't experienced yet reads as
# growth-hungry rather than confident in its own teaching — this is checked
# BEFORE ever offering the option, not just before nudging about it later.
REFERRAL_ACTIVE_WITHIN_DAYS = 7
REFERRAL_STREAK_THRESHOLD_DAYS = 3
REFERRAL_WEEKLY_MESSAGE_THRESHOLD = 10


def grant_signup_referral_bonus(db: Session, referrer_id: int) -> bool:
    """
    Pays REFERRAL_SIGNUP_BONUS, capped at MAX_SIGNUP_BONUSES_PER_REFERRER_PER_DAY
    per referrer per rolling day — see that constant for why only this
    particular milestone needs a cap. Returns whether it was actually paid,
    so the caller (whatsapp.py's _create_new_student) only sends the "you
    earned a bonus" WhatsApp notification when something real happened.
    """
    since = datetime.now(timezone.utc) - timedelta(days=1)
    recent_count = (
        db.query(CreditEvent)
        .filter(
            CreditEvent.student_id == referrer_id,
            CreditEvent.service == cost_tracker.REFERRAL_BONUS_SERVICE,
            CreditEvent.note == _SIGNUP_BONUS_NOTE,
            CreditEvent.created_at >= since,
        )
        .count()
    )
    if recent_count >= MAX_SIGNUP_BONUSES_PER_REFERRER_PER_DAY:
        return False
    cost_tracker.grant_referral_credit(db, referrer_id, REFERRAL_SIGNUP_BONUS, note=_SIGNUP_BONUS_NOTE)
    return True


async def try_claim_late_referral(db: Session, student: Student, message_text: str) -> bool:
    """
    Catches a referral code sent in one of a new student's early messages
    rather than their very first one (see LATE_REFERRAL_CLAIM_WINDOW above
    for why this is time-bounded). Call on every inbound message from a
    student who doesn't already have referred_by_id set — it's cheap to
    no-op (no regex match on almost every real message, and the window
    check short-circuits before any query once a student ages out).
    Returns whether a referral was actually linked, so the caller can
    reply distinctly instead of falling through to a normal tutoring turn.
    """
    if student.referred_by_id is not None:
        return False
    created_at = student.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created_at > LATE_REFERRAL_CLAIM_WINDOW:
        return False
    referral_code = extract_referral_code(message_text)
    if not referral_code:
        return False
    referrer = db.query(Student).filter(Student.referral_code == referral_code, Student.id != student.id).first()
    if referrer is None:
        return False

    student.referred_by_id = referrer.id
    db.commit()
    paid = grant_signup_referral_bonus(db, referrer.id)
    if paid:
        try:
            await send_whatsapp_message(
                referrer.phone,
                f"🎉 Your friend just joined using your referral code! You've earned "
                f"₹{REFERRAL_SIGNUP_BONUS:.0f} in bonus AI credits.",
            )
        except Exception:
            pass
    return True


def is_worth_asking_to_refer(activity: dict, weekly_messages_sent: int) -> bool:
    """
    activity is app.services.progress_report.get_activity_stats' return
    dict; weekly_messages_sent is get_student_stats(..., days=7)["messages_sent"].
    """
    if activity["days_since_last_message"] is None or activity["days_since_last_message"] > REFERRAL_ACTIVE_WITHIN_DAYS:
        return False
    return (
        activity["streak_days"] >= REFERRAL_STREAK_THRESHOLD_DAYS
        or weekly_messages_sent >= REFERRAL_WEEKLY_MESSAGE_THRESHOLD
    )


def generate_referral_code(student_id: int) -> str:
    # Offset so codes read as a plausible-looking short code rather than
    # exposing a tiny raw row id (e.g. "QL1" for the 1st student ever).
    return f"{REFERRAL_CODE_PREFIX}{1000 + student_id}"


def extract_referral_code(text: str) -> str | None:
    match = _CODE_PATTERN.search(text.upper())
    return f"{REFERRAL_CODE_PREFIX}{match.group(1)}" if match else None


async def evaluate_referral_milestones(db: Session, student: Student) -> None:
    """
    Called on every real tutoring question a referred student sends (see
    whatsapp.py). Checks whichever day/week window "now" falls into against
    REFERRAL_MILESTONES and pays the referrer once per milestone the first
    time its activity threshold is met — cheap to call every turn since it
    no-ops immediately once every window has either been paid or passed.
    """
    if not student.referred_by_id:
        return

    paid = set(student.referral_milestones_paid or [])
    if len(paid) == len(REFERRAL_MILESTONES):
        return  # every milestone already settled — skip the query entirely

    created_at = student.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    elapsed_days = (datetime.now(timezone.utc) - created_at).days

    changed = False
    for name, start, end, min_questions, bonus in REFERRAL_MILESTONES:
        if name in paid or not (start <= elapsed_days < end):
            continue
        window_start = created_at + timedelta(days=start)
        window_end = created_at + timedelta(days=end)
        count = (
            db.query(ChatHistory)
            .filter(
                ChatHistory.student_id == student.id,
                ChatHistory.role == "user",
                ChatHistory.created_at >= window_start,
                ChatHistory.created_at < window_end,
            )
            .count()
        )
        if count >= min_questions:
            cost_tracker.grant_referral_credit(
                db, student.referred_by_id, bonus, note=f"Referral milestone: {name}"
            )
            paid.add(name)
            changed = True
            # The referrer isn't in this conversation at all (this call is
            # triggered by the REFERRED student's activity), so a silent
            # ledger credit is the only way they'd ever see it — actively
            # notify them instead. Best-effort: shouldn't break the
            # referred student's own reply if this send fails.
            referrer = db.query(Student).filter(Student.id == student.referred_by_id).first()
            if referrer is not None:
                try:
                    await send_whatsapp_message(
                        referrer.phone,
                        f"🎉 Your referral is paying off! {student.name} has been active, so you've "
                        f"earned ₹{bonus:.0f} in bonus AI credits.",
                    )
                except Exception:
                    pass

    if changed:
        student.referral_milestones_paid = list(paid)
        db.commit()
