"""
Sends a spaced-repetition revision reminder for each (student, topic) whose
revision_schedule row is due — see app.services.revision_scheduler for the
Leitner-style interval ladder that decides when a topic comes due, driven by
right/wrong answers recorded in quiz_flow.py / chat_core.py.

Sending a reminder does NOT itself advance the schedule — the ladder only
moves when the student actually answers something on that topic again (via
revision_scheduler.on_topic_result). This job just nudges; it doesn't
"consume" the review.

No Celery/scheduler is wired up in this project yet, so this is meant to be
run once a day via an external cron, the same pattern as
scripts/send_reengagement_nudges.py:

    0 12 * * * cd /path/to/qlass-ai && venv/bin/python3 scripts/send_revision_reminders.py >> logs/cron-revision.log 2>&1

Usage:
    python scripts/send_revision_reminders.py [--dry-run]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Student  # noqa: E402
from app.services import revision_scheduler  # noqa: E402
from app.services.whatsapp_client import send_whatsapp_message  # noqa: E402


def _format_reminder(student: Student, topic: str) -> str:
    return (
        f"Hey {student.name}! 👋 It's been a little while since we covered *{topic}* — "
        f"a quick revisit now will help it stick. Just ask me about it, or say "
        f"\"quiz me on {topic}\" and I'll test you on the spot. 📚"
    )


async def send_reminders(dry_run: bool) -> None:
    db = SessionLocal()
    sent = 0
    try:
        due_rows = revision_scheduler.get_due_reviews(db)
        if not due_rows:
            print("No revision reminders due.")
            return

        student_ids = {row.student_id for row in due_rows}
        students_by_id = {
            student.id: student
            for student in (
                db.query(Student)
                .filter(Student.id.in_(student_ids), Student.is_deleted.is_(False), Student.is_staff_profile.is_(False))
                .all()
            )
        }

        for row in due_rows:
            student = students_by_id.get(row.student_id)
            if student is None:
                continue
            message = _format_reminder(student, row.topic)

            if dry_run:
                print(f"[DRY RUN] Would remind {student.phone} ({student.name}) to revise *{row.topic}*: {message}")
            else:
                result = await send_whatsapp_message(student.phone, message)
                print(f"{'Sent' if result.get('sent') else 'FAILED'} revision reminder to {student.phone} ({student.name}) on '{row.topic}'")
            sent += 1
        print(f"\n{sent} revision reminder(s) {'would be ' if dry_run else ''}sent.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be sent without actually sending")
    args = parser.parse_args()
    asyncio.run(send_reminders(args.dry_run))


if __name__ == "__main__":
    main()
