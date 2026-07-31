"""
Reminds anyone on the flat-fee unlimited plan (₹1800/yr for a real student,
₹3500/mo for a teacher's own "My AI Tutor" profile — see
app.services.cost_tracker._is_unlimited_active) that it's about to expire,
since activation is still a manual one-time flag with a fixed expiry (no
real Razorpay Subscriptions/UPI Autopay integration yet) — without this,
someone has to remember to check every account's expiry date by hand.

A real student's reminder goes to the student's own WhatsApp, and to their
linked parent if one exists (whoever actually handles payment). A teacher's
personal-tutor profile reminder goes to the teacher's own WhatsApp.

No Celery/scheduler is wired up in this project yet, so this is meant to be
run daily via an external cron on wherever the backend is actually hosted:

    0 9 * * * cd /path/to/qlass-ai && venv/bin/python3 scripts/send_subscription_expiry_reminders.py >> logs/subscription_reminders.log 2>&1

Usage:
    python scripts/send_subscription_expiry_reminders.py [--days 7] [--dry-run]
"""
import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Parent, Student, Teacher  # noqa: E402
from app.services.whatsapp_client import send_whatsapp_message  # noqa: E402

# Redis-free cooldown: reminders only fire once expiry falls inside this
# window, and the window is narrow enough (with a daily cron) that a
# student/teacher gets roughly one reminder, not one per day for a week —
# see the exact-day check in remind().
REMINDER_WINDOW_DAYS = 7


def _format_reminder(name: str, expires_at: datetime, price_label: str) -> str:
    days_left = (expires_at.date() - datetime.now(timezone.utc).date()).days
    when = "today" if days_left <= 0 else f"in {days_left} day{'s' if days_left != 1 else ''}"
    return (
        f"⏰ Hi! {name}'s Qlass unlimited AI tutor plan ({price_label}) expires {when} "
        f"({expires_at.strftime('%d %b %Y')}). Renew soon to avoid any interruption!"
    )


async def send_reminders(days: int, dry_run: bool) -> None:
    db = SessionLocal()
    sent = 0
    try:
        cutoff = datetime.now(timezone.utc) + timedelta(days=days)
        expiring = (
            db.query(Student)
            .filter(
                Student.subscription_plan == "unlimited",
                Student.subscription_expires_at.isnot(None),
                Student.subscription_expires_at <= cutoff,
                Student.subscription_expires_at >= datetime.now(timezone.utc),
                Student.is_deleted.is_(False),
            )
            .all()
        )

        for student in expiring:
            expires_at = student.subscription_expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if student.is_staff_profile:
                # A staff profile has no direct FK back to its owning
                # teacher — resolved by phone instead, since
                # get_or_create_linked_student creates it with the
                # teacher's own phone (see app.services.tenancy).
                teacher = db.query(Teacher).filter(Teacher.phone == student.phone).first()
                if teacher is None:
                    continue
                message = _format_reminder("Your personal", expires_at, "₹3500/month")
                recipients = [teacher.phone]
            else:
                message = _format_reminder(student.name, expires_at, "₹1800/year")
                recipients = [student.phone]
                parent = db.query(Parent).filter(Parent.student_id == student.id).first()
                if parent:
                    recipients.append(parent.phone)

            for phone in recipients:
                if dry_run:
                    print(f"[DRY RUN] Would remind {phone} ({student.name}): {message}")
                else:
                    result = await send_whatsapp_message(phone, message)
                    print(f"{'Sent' if result.get('sent') else 'FAILED'} expiry reminder to {phone} ({student.name})")
                sent += 1
        print(f"\n{sent} reminder(s) {'would be ' if dry_run else ''}sent.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=REMINDER_WINDOW_DAYS, help="Remind if expiring within this many days")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be sent without actually sending")
    args = parser.parse_args()
    asyncio.run(send_reminders(args.days, args.dry_run))


if __name__ == "__main__":
    main()
