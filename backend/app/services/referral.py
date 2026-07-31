import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.core import ChatHistory, Student
from app.services import cost_tracker

REFERRAL_CODE_PREFIX = "QL"
_CODE_PATTERN = re.compile(r"\bQL(\d{4,})\b")

# Paid the moment someone signs up mentioning a referral code — see
# whatsapp.py's _create_new_student. Not gated on any activity.
REFERRAL_SIGNUP_BONUS = 10.0

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


def generate_referral_code(student_id: int) -> str:
    # Offset so codes read as a plausible-looking short code rather than
    # exposing a tiny raw row id (e.g. "QL1" for the 1st student ever).
    return f"{REFERRAL_CODE_PREFIX}{1000 + student_id}"


def extract_referral_code(text: str) -> str | None:
    match = _CODE_PATTERN.search(text.upper())
    return f"{REFERRAL_CODE_PREFIX}{match.group(1)}" if match else None


def evaluate_referral_milestones(db: Session, student: Student) -> None:
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

    if changed:
        student.referral_milestones_paid = list(paid)
        db.commit()
