"""
Send a weekly progress digest for one or more students to a teacher/parent's
WhatsApp number — reuses the same stats the student's own "how am I doing?"
command is built on (see app.services.progress_report), just scoped to the
last 7 days and formatted for a teacher rather than the student themselves.

No Teacher/Parent onboarding UI or scheduler exists yet, so this is meant to
be run manually, or wired to an external cron once deployed
(e.g. `0 8 * * MON python scripts/send_teacher_digest.py --to <phone>
--students <phone1> <phone2> ...`).

Usage:
    python scripts/send_teacher_digest.py --to <teacher_phone> --students <student_phone> [<student_phone> ...]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Student  # noqa: E402
from app.services.progress_report import get_student_stats, format_teacher_digest  # noqa: E402
from app.services.whatsapp_client import send_whatsapp_message  # noqa: E402


async def send_digest(teacher_phone: str, student_phones: list[str]) -> None:
    db = SessionLocal()
    try:
        sections = []
        for phone in student_phones:
            student = db.query(Student).filter(Student.phone == phone).first()
            if not student:
                print(f"Skipping unknown phone {phone}")
                continue
            stats = get_student_stats(db, student.id, days=7)
            sections.append(format_teacher_digest(
                student.name, stats,
                hints_given=student.hints_given_count or 0,
                direct_solutions=student.direct_solutions_count or 0,
            ))

        if not sections:
            print("No valid students — nothing to send.")
            return

        message = "📋 *Weekly Student Digest*\n\n" + "\n\n".join(sections)
        result = await send_whatsapp_message(teacher_phone, message)
        if result.get("sent"):
            print(f"Digest sent to {teacher_phone}.")
        else:
            print(f"Failed to send digest: {result}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", required=True, help="Teacher/parent WhatsApp number to send the digest to")
    parser.add_argument("--students", required=True, nargs="+", help="Student phone number(s) to include")
    args = parser.parse_args()
    asyncio.run(send_digest(args.to, args.students))


if __name__ == "__main__":
    main()
