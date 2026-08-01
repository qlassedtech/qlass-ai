"""
Sends a motivating nudge to students who were genuinely engaged before but
have gone quiet for a few days — the opposite targeting from
send_referral_nudges.py (which only targets currently-active students).
Personalized with whatever topic they were last working on, so it reads as
"come back to what you were doing" rather than a generic re-engagement
blast. Only targets students who were actually active before (a brand new
student who never engaged isn't "coming back" to anything) and stops once
they've gone quiet long enough that a nudge is unlikely to help (see
MAX_QUIET_DAYS) — that's a job for a human/school outreach, not an
automated WhatsApp message.

No Celery/scheduler is wired up in this project yet, so this is meant to be
run once a day via an external cron, the same pattern as
scripts/send_habit_nudges.py:

    0 17 * * * cd /path/to/qlass-ai && venv/bin/python3 scripts/send_reengagement_nudges.py >> logs/reengagement_nudges.log 2>&1

Usage:
    python scripts/send_reengagement_nudges.py [--dry-run]
"""
import argparse
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import redis.asyncio as redis  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.core import Student, TopicProgress  # noqa: E402
from app.services.progress_report import get_activity_stats  # noqa: E402
from app.services.whatsapp_client import send_whatsapp_message  # noqa: E402

# Quiet window this nudge targets — under MIN_QUIET_DAYS, get_welcome_back_note
# already handles it naturally the moment they message again on their own;
# past MAX_QUIET_DAYS, a single WhatsApp nudge is unlikely to bring someone
# back and repeatedly pinging a cold contact just reads as spam.
MIN_QUIET_DAYS = 3
MAX_QUIET_DAYS = 10
# "Was genuinely engaged" proxy — at least this many distinct days of real
# activity, ever. Excludes a student who tried it once or twice and drifted
# off; this nudge is for someone who built a real habit and paused, not
# someone who never really started.
ENGAGEMENT_THRESHOLD_DAYS = 4
# Don't nudge the same student more than once in this many days, even if
# they stay in the quiet window across multiple cron runs.
NUDGE_COOLDOWN_SECONDS = 5 * 24 * 3600

_MOTIVATIONAL_LINES = [
    "A little bit every day adds up faster than you'd think.",
    "The best time to pick something back up is right when you think about it.",
    "Small steps now save a lot of stress later.",
    "You were on a good run — easy to get back into it.",
]

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True) if settings.redis_url else None
_fallback_sent: set[str] = set()  # per-process only, if Redis isn't reachable


async def _recently_nudged(phone: str) -> bool:
    key = f"reengagement_nudge_sent:{phone}"
    if _redis is None:
        return phone in _fallback_sent
    return await _redis.get(key) is not None


async def _mark_nudged(phone: str) -> None:
    key = f"reengagement_nudge_sent:{phone}"
    if _redis is None:
        _fallback_sent.add(phone)
        return
    await _redis.set(key, "1", ex=NUDGE_COOLDOWN_SECONDS)


def _last_topic(db, student: Student) -> str | None:
    if student.last_discussed_topic:
        return student.last_discussed_topic
    row = (
        db.query(TopicProgress.topic)
        .filter(TopicProgress.student_id == student.id)
        .order_by(TopicProgress.created_at.desc())
        .first()
    )
    return row[0] if row else None


def _format_nudge(student: Student, gap_days: int, topic: str | None) -> str:
    line = random.choice(_MOTIVATIONAL_LINES)
    if topic:
        return (
            f"Hey {student.name}! 👋 It's been {gap_days} days since we last worked on *{topic}* together. "
            f"{line} Want to pick up right where we left off? I can help you get through it faster than "
            f"going it alone. 💪"
        )
    return (
        f"Hey {student.name}! 👋 It's been {gap_days} days since we last chatted. {line} "
        f"Ask me anything — I'm here to help you learn faster. 💪"
    )


async def send_nudges(dry_run: bool) -> None:
    db = SessionLocal()
    sent = 0
    try:
        students = (
            db.query(Student)
            .filter(Student.is_staff_profile.is_(False), Student.is_deleted.is_(False))
            .all()
        )
        for student in students:
            activity = get_activity_stats(db, student.id)
            gap = activity["days_since_last_message"]
            if gap is None or not (MIN_QUIET_DAYS <= gap <= MAX_QUIET_DAYS):
                continue
            if activity["active_days"] < ENGAGEMENT_THRESHOLD_DAYS:
                continue  # never built a real habit — not "coming back" to anything
            if await _recently_nudged(student.phone):
                continue

            topic = _last_topic(db, student)
            message = _format_nudge(student, gap, topic)

            if dry_run:
                print(f"[DRY RUN] Would nudge {student.phone} ({student.name}), {gap}d quiet: {message}")
            else:
                result = await send_whatsapp_message(student.phone, message)
                print(f"{'Sent' if result.get('sent') else 'FAILED'} re-engagement nudge to {student.phone} ({student.name})")
                if result.get("sent"):
                    await _mark_nudged(student.phone)
            sent += 1
        print(f"\n{sent} re-engagement nudge(s) {'would be ' if dry_run else ''}sent.")
    finally:
        db.close()
        if _redis is not None:
            await _redis.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be sent without actually sending")
    args = parser.parse_args()
    asyncio.run(send_nudges(args.dry_run))


if __name__ == "__main__":
    main()
