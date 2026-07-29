"""
Sends a weekly progress digest directly to every linked parent's own
WhatsApp — unlike scripts/send_teacher_digest.py (manual --to/--students
invocation, one recipient at a time), this automatically loops over EVERY
Parent row in the database, since most parents in this market will never
log into the web dashboard but are already reachable on WhatsApp.

No Celery/scheduler is wired up in this project yet, so this is meant to
be run weekly via an external cron on wherever the backend is actually
hosted (not a local dev machine):

    0 18 * * FRI cd /path/to/qlass-ai && venv/bin/python3 scripts/send_parent_digests.py >> logs/parent_digests.log 2>&1

Usage:
    python scripts/send_parent_digests.py [--dry-run]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Parent, Student  # noqa: E402
from app.services.progress_report import format_parent_digest, get_activity_stats, get_student_stats  # noqa: E402
from app.services.whatsapp_client import send_whatsapp_message  # noqa: E402

DIGEST_WINDOW_DAYS = 7


async def send_digests(dry_run: bool) -> None:
    db = SessionLocal()
    sent = 0
    try:
        parents = db.query(Parent).all()
        for parent in parents:
            student = db.query(Student).filter(Student.id == parent.student_id, Student.is_deleted.is_(False)).first()
            if student is None:
                continue  # linked student's data-deletion request has been fulfilled, or row is stale

            stats = get_student_stats(db, student.id, days=DIGEST_WINDOW_DAYS)
            activity = get_activity_stats(db, student.id)
            message = format_parent_digest(student.name, stats, activity)

            if dry_run:
                print(f"[DRY RUN] Would send to {parent.phone} (parent of {student.name}):\n{message}\n")
            else:
                result = await send_whatsapp_message(parent.phone, message)
                print(f"{'Sent' if result.get('sent') else 'FAILED'} parent digest to {parent.phone} ({student.name})")
            sent += 1
        print(f"\n{sent} parent digest(s) {'would be ' if dry_run else ''}sent.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be sent without actually sending")
    args = parser.parse_args()
    asyncio.run(send_digests(args.dry_run))


if __name__ == "__main__":
    main()
