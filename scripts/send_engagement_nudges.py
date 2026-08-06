"""
Cron entry point for proactive WhatsApp re-engagement nudges (see
app.services.nudges) — meant to run once a day (e.g. via a system crontab
entry: `0 17 * * * cd /path/to/backend && ../venv/bin/python
../scripts/send_engagement_nudges.py`), not something the FastAPI process
schedules itself (no scheduler infra exists in this codebase — see
app.main, whose only background task is the WhatsApp webhook retry loop).

Finds students who've gone quiet for INACTIVITY_DAYS+, skips anyone who's
opted out, churned, out of credits, or a teacher's own staff profile (a
teacher testing their own "My AI Tutor" isn't a re-engagement target), and
sends whichever nudge type app.services.nudges.pick_next_nudge picks for
each. Requires ENGAGEMENT_NUDGE_TEMPLATE_NAME to be an approved Wati
template — see app.services.nudges' module docstring for the exact
{{1}}-variable format needed. Until that template is approved, this script
runs but every send attempt fails cleanly (send_template_message returns
{"sent": False, ...}) and is logged, not silently swallowed.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.core import Student  # noqa: E402
from app.services import cost_tracker, school_billing  # noqa: E402
from app.services.nudges import ENGAGEMENT_NUDGE_TEMPLATE_NAME, pick_next_nudge, record_nudge_sent  # noqa: E402
from app.services.whatsapp_client import send_template_message  # noqa: E402

INACTIVITY_DAYS = 2
# A real per-run cap, same reasoning as broadcast.py's MAX_BROADCAST_RECIPIENTS
# — a filter/query bug here shouldn't be able to message the entire student
# base in one run before anyone notices.
MAX_SENDS_PER_RUN = 2000
# Wati doesn't document a hard rate limit for this endpoint (see
# app.services.whatsapp_client.send_template_message) — a small delay
# between sends is a conservative, low-cost way to avoid tripping one.
SEND_DELAY_SECONDS = 0.3


def _find_inactive_students(db) -> list[Student]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=INACTIVITY_DAYS)
    rows = db.execute(
        text(
            """
            SELECT s.id FROM students s
            WHERE s.is_staff_profile IS NOT TRUE
              AND s.is_deleted IS NOT TRUE
              AND s.nudges_opt_out IS NOT TRUE
              AND s.created_at < :cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM chat_history ch
                  WHERE ch.student_id = s.id AND ch.role = 'user' AND ch.created_at >= :cutoff
              )
            LIMIT :limit
            """
        ),
        {"cutoff": cutoff, "limit": MAX_SENDS_PER_RUN},
    ).fetchall()
    student_ids = [row[0] for row in rows]
    if not student_ids:
        return []
    return db.query(Student).filter(Student.id.in_(student_ids)).all()


async def run() -> None:
    db = SessionLocal()
    sent, skipped_no_content, skipped_gate, failed = 0, 0, 0, 0
    try:
        students = _find_inactive_students(db)
        print(f"{len(students)} inactive student(s) found (quiet {INACTIVITY_DAYS}+ days).")
        for student in students:
            # Nudging someone who can't act on it (school churned, or out
            # of credits with no way to keep chatting even if they come
            # back) just wastes the message — same real-world gating the
            # actual chat path already applies (see app.routers.whatsapp).
            if school_billing.is_centre_churned(db, student.centre_id) and not cost_tracker.has_independent_payment(db, student.id):
                skipped_gate += 1
                continue
            if not cost_tracker.has_credits(db, student.id):
                skipped_gate += 1
                continue

            picked = await pick_next_nudge(db, student)
            if picked is None:
                skipped_no_content += 1
                continue
            nudge_type, message = picked

            result = await send_template_message(student.phone, ENGAGEMENT_NUDGE_TEMPLATE_NAME, [{"name": "1", "value": message}])
            if result.get("sent"):
                record_nudge_sent(db, student, nudge_type)
                sent += 1
            else:
                print(f"FAILED to send {nudge_type} nudge to student_id={student.id}: {result.get('reason')}")
                failed += 1
            await asyncio.sleep(SEND_DELAY_SECONDS)
    finally:
        db.close()

    print(f"\nDone: {sent} sent, {failed} failed, {skipped_no_content} skipped (no eligible nudge content), {skipped_gate} skipped (churned/no credits).")


if __name__ == "__main__":
    asyncio.run(run())
