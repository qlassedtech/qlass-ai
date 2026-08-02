"""
Sends a WhatsApp nudge encouraging a student to refer a friend — targeted
at students who are both ACTIVE (messaged recently) and clearly getting
real value (a solid streak or a lot of messages this week), since they're
the most credible, highest-conversion candidates to ask. Unlike
send_habit_nudges.py, referral credit isn't tied to a fixed day-window, so
this uses a Redis cooldown instead (default 14 days) to avoid asking the
same student too often.

No Celery/scheduler is wired up in this project yet, so this is meant to
be run periodically via an external cron on wherever the backend is
actually hosted (not a local dev machine) — Sunday is the recommended day,
since students/parents have more free time to actually message a friend
about it than on a school day:

    0 10 * * SUN cd /path/to/qlass-ai && venv/bin/python3 scripts/send_referral_nudges.py >> logs/referral_nudges.log 2>&1

Usage:
    python scripts/send_referral_nudges.py [--dry-run]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import redis.asyncio as redis  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.core import Student  # noqa: E402
from app.services.progress_report import get_activity_stats, get_student_stats  # noqa: E402
from app.services.referral import generate_referral_code, is_worth_asking_to_refer  # noqa: E402
from app.services.whatsapp_client import send_whatsapp_message  # noqa: E402

# Don't ask the same student again for this long after a nudge.
NUDGE_COOLDOWN_SECONDS = 14 * 24 * 3600

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True) if settings.redis_url else None
_fallback_sent: set[str] = set()  # per-process only, if Redis isn't reachable


async def _recently_nudged(phone: str) -> bool:
    key = f"referral_nudge_sent:{phone}"
    if _redis is None:
        return phone in _fallback_sent
    return await _redis.get(key) is not None


async def _mark_nudged(phone: str) -> None:
    key = f"referral_nudge_sent:{phone}"
    if _redis is None:
        _fallback_sent.add(phone)
        return
    await _redis.set(key, "1", ex=NUDGE_COOLDOWN_SECONDS)


async def send_nudges(dry_run: bool) -> None:
    db = SessionLocal()
    sent = 0
    try:
        students = db.query(Student).filter(Student.is_staff_profile.is_(False)).all()
        for student in students:
            activity = get_activity_stats(db, student.id)
            weekly_stats = get_student_stats(db, student.id, days=7)
            if not is_worth_asking_to_refer(activity, weekly_stats["messages_sent"]):
                continue

            if await _recently_nudged(student.phone):
                continue

            if not student.referral_code:
                student.referral_code = generate_referral_code(student.id)
                db.commit()

            message = (
                f"Hey {student.name}! 🌟 You've been on a real roll — "
                f"{activity['streak_days']}-day streak, {weekly_stats['messages_sent']} messages this week. "
                f"Know a friend who could use help with homework? Share your code *{student.referral_code}* — "
                f"once they start chatting with me, you'll earn AI credits! 🎁"
            )
            if dry_run:
                print(f"[DRY RUN] Would nudge {student.phone} ({student.name}): {message}")
            else:
                result = await send_whatsapp_message(student.phone, message)
                print(f"{'Sent' if result.get('sent') else 'FAILED'} referral nudge to {student.phone} ({student.name})")
                if result.get("sent"):
                    await _mark_nudged(student.phone)
            sent += 1
        print(f"\n{sent} referral nudge(s) {'would be ' if dry_run else ''}sent.")
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
