"""
Spaced-repetition scheduling for weak topics, driven by TopicProgress
results. Uses a fixed Leitner-style interval ladder (days: 1, 3, 7, 16, 35)
rather than full SM-2 with per-item ease factors — deterministic, easy to
reason about/debug, and good enough for a first version of proactive
revision nudges.
"""
from datetime import datetime, timedelta, timezone

from app.models.core import RevisionSchedule

LADDER_DAYS = [1, 3, 7, 16, 35]


def on_topic_result(db, student_id: int, topic: str, is_correct: bool) -> None:
    now = datetime.now(timezone.utc)
    row = (
        db.query(RevisionSchedule)
        .filter(RevisionSchedule.student_id == student_id, RevisionSchedule.topic == topic)
        .first()
    )

    if is_correct:
        if row is None:
            # Nothing to advance — a topic answered correctly with no prior
            # schedule doesn't need one.
            return
        row.interval_stage = min(row.interval_stage + 1, len(LADDER_DAYS) - 1)
        row.due_at = now + timedelta(days=LADDER_DAYS[row.interval_stage])
        row.last_reviewed_at = now
    else:
        if row is None:
            row = RevisionSchedule(student_id=student_id, topic=topic)
            db.add(row)
        row.interval_stage = 0
        row.due_at = now + timedelta(days=LADDER_DAYS[0])
        row.last_reviewed_at = now

    db.commit()


def get_due_reviews(db, cutoff: datetime | None = None) -> list[RevisionSchedule]:
    cutoff = cutoff or datetime.now(timezone.utc)
    return (
        db.query(RevisionSchedule)
        .filter(RevisionSchedule.due_at <= cutoff)
        .all()
    )
