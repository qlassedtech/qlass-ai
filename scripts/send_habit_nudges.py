"""
Sends a WhatsApp nudge to students who are inside one of the 21-day habit
milestone windows (day1/3/7/14/21 — see app.services.habit) but haven't
asked a question yet today, encouraging them to chat today to earn that
milestone's credit bonus. The credit itself is granted reactively the
moment they actually engage (evaluate_habit_milestones, checked live on
every message) — this script only sends the reminder, it never grants
credit itself.

No Celery/scheduler is wired up in this project yet, so this is meant to
be run once a day via an external cron
(e.g. `0 9 * * * python scripts/send_habit_nudges.py`), the same pattern as
scripts/send_teacher_digest.py.

Usage:
    python scripts/send_habit_nudges.py [--dry-run]
"""
import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import ChatHistory, Student  # noqa: E402
from app.services.habit import HABIT_MILESTONES  # noqa: E402
from app.services.whatsapp_client import send_whatsapp_message  # noqa: E402


def _has_engaged_in_window(db, student_id: int, window_start: datetime, window_end: datetime) -> bool:
    return (
        db.query(ChatHistory)
        .filter(
            ChatHistory.student_id == student_id, ChatHistory.role == "user",
            ChatHistory.created_at >= window_start, ChatHistory.created_at < window_end,
        )
        .first()
        is not None
    )


async def send_nudges(dry_run: bool) -> None:
    db = SessionLocal()
    sent = 0
    try:
        students = db.query(Student).all()
        now = datetime.now(timezone.utc)
        for student in students:
            paid = set(student.habit_milestones_paid or [])
            if len(paid) == len(HABIT_MILESTONES):
                continue

            created_at = student.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            elapsed_days = (now - created_at).days

            for name, start, end, bonus in HABIT_MILESTONES:
                if name in paid or not (start <= elapsed_days < end):
                    continue
                window_start = created_at + timedelta(days=start)
                window_end = created_at + timedelta(days=end)
                if _has_engaged_in_window(db, student.id, window_start, window_end):
                    continue  # already earning it today via a real message

                day_label = name.replace("day", "Day ")
                message = (
                    f"Hey {student.name}! 👋 You're on {day_label} of your learning streak. "
                    f"Ask me a question today and earn ₹{bonus:.0f} in AI credits! 🎯"
                )
                if dry_run:
                    print(f"[DRY RUN] Would nudge {student.phone} ({student.name}) — {name}: {message}")
                else:
                    result = await send_whatsapp_message(student.phone, message)
                    print(f"{'Sent' if result.get('sent') else 'FAILED'} nudge to {student.phone} ({name})")
                sent += 1
                break  # one nudge per student per run, even if multiple windows somehow overlap
        print(f"\n{sent} nudge(s) {'would be ' if dry_run else ''}sent.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be sent without actually sending")
    args = parser.parse_args()
    asyncio.run(send_nudges(args.dry_run))


if __name__ == "__main__":
    main()
