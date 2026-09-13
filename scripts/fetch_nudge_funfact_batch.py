"""
Fetch phase of the "fun_fact" re-engagement nudge batch pipeline (see
app.services.nudges module docstring and scripts/submit_nudge_funfact_batch.py
for the submit side). Checks every NudgeFunFactBatch row still in
"submitted" state: if Anthropic hasn't finished processing it yet, logs and
moves on (most batches finish well under an hour; SLA is up to 24h — see
https://docs.anthropic.com/en/docs/build-with-claude/batch-processing);
if it has, retrieves results, sends each student their WhatsApp nudge via
the existing send_template_message path, records real spend via
cost_tracker.record_platform_claude_usage(batch=True), and marks the batch
"processed".

Idempotent by design: only "submitted" rows are considered, and a batch is
flipped to "processed" only after every one of its students has been
handled (sent or explicitly skipped) — so re-running this script (e.g. the
next cron slot, as a natural retry for a batch that wasn't ready yet) never
re-sends anything for a batch already marked "processed", and a mid-run
crash before that final commit just means the WHOLE batch gets reprocessed
next time (a student who already got the nudge in the crashed run would
now fail eligible_for_fun_fact's cooldown check and be skipped on the
retry — see the per-student re-check below — rather than getting nudged
twice; worst case is a wasted batch-results read, not a duplicate send).

Intended cron slot: run ~75 minutes after the submit script, e.g.

    15 17 * * *  cd $APP && $PY scripts/fetch_nudge_funfact_batch.py >> logs/cron-nudge-funfact-fetch.log 2>&1

and again later as a cheap retry for anything not yet "ended", e.g.

    0 22 * * *   cd $APP && $PY scripts/fetch_nudge_funfact_batch.py >> logs/cron-nudge-funfact-fetch-retry.log 2>&1

One nuance vs. the submit script's docstring: because eligible_for_fun_fact
is re-checked here (not just at submit time), a student who somehow becomes
ineligible in the gap between submission and fetch (opted out, went back
on cooldown some other way, churned, ran out of credits) is skipped rather
than sent — this is a deliberate safety re-check, not a bug; the ~1 hour
submit-to-fetch gap is short but non-zero.
"""
import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from anthropic import Anthropic  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.core import NudgeFunFactBatch, Student  # noqa: E402
from app.services import cost_tracker, school_billing  # noqa: E402
from app.services.nudges import (  # noqa: E402
    NO_FACT_SENTINEL,
    eligible_for_fun_fact,
    record_nudge_sent,
    template_for_nudge_type,
)
from app.services.whatsapp_client import send_template_message  # noqa: E402

# Same reasoning as scripts/send_engagement_nudges.py's SEND_DELAY_SECONDS.
SEND_DELAY_SECONDS = 0.3


def _text_of(message) -> str:
    return "".join(block.text for block in message.content if block.type == "text").strip()


async def _process_batch(db, batch_row: NudgeFunFactBatch, client: Anthropic) -> None:
    remote = client.messages.batches.retrieve(batch_row.batch_id)
    if remote.processing_status != "ended":
        print(f"Batch {batch_row.batch_id}: still {remote.processing_status} — skipping for now.")
        return

    results_by_custom_id = {result.custom_id: result for result in client.messages.batches.results(batch_row.batch_id)}

    by_student: dict[int, list[dict]] = defaultdict(list)
    for entry in batch_row.requests:
        by_student[entry["student_id"]].append(entry)
    for entries in by_student.values():
        entries.sort(key=lambda e: e["custom_id"])  # "student-{id}-attempt-{n}" — preserves attempt order

    sent = skipped = failed = 0
    for student_id, entries in by_student.items():
        student = db.query(Student).filter(Student.id == student_id).first()
        if student is None:
            skipped += 1
            continue
        # Safety re-check — see module docstring. Cheap and avoids sending
        # to someone who's since opted out, churned, run dry on credits, or
        # (for any reason) is no longer due a fun_fact nudge.
        if school_billing.is_centre_churned(db, student.centre_id) and not cost_tracker.has_independent_payment(db, student.id):
            skipped += 1
            continue
        if not cost_tracker.has_credits(db, student.id):
            skipped += 1
            continue
        if not eligible_for_fun_fact(student):
            skipped += 1
            continue

        message_text, chapter = None, None
        for entry in entries:
            result = results_by_custom_id.get(entry["custom_id"])
            if result is None:
                continue
            if result.result.type != "succeeded":
                # errored/canceled/expired — same handling as a rejected
                # NO_FACT draw below: try the next candidate chunk for this
                # student, don't fail the whole student over one bad request.
                continue
            msg = result.result.message
            usage = msg.usage
            # Billed as platform-absorbed (amount=0) at HALF the standard
            # rate (batch=True) — see cost_tracker.record_platform_claude_usage.
            cost_tracker.record_platform_claude_usage(
                db, msg.model, usage.input_tokens, usage.output_tokens, student.id,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                feature="nudge_funfact", batch=True,
            )
            text = _text_of(msg)
            if text and NO_FACT_SENTINEL not in text:
                message_text, chapter = text, entry["chapter"]
                break

        if message_text is None:
            skipped += 1
            continue

        result_send = await send_template_message(
            student.phone, template_for_nudge_type("fun_fact"), [{"name": "1", "value": message_text}]
        )
        if result_send.get("sent"):
            record_nudge_sent(db, student, "fun_fact", chapter)
            sent += 1
        else:
            print(f"FAILED to send fun_fact nudge to student_id={student.id}: {result_send.get('reason')}")
            failed += 1
        await asyncio.sleep(SEND_DELAY_SECONDS)

    batch_row.status = "processed"
    batch_row.processed_at = datetime.now(timezone.utc)
    db.commit()
    print(f"Batch {batch_row.batch_id}: {sent} sent, {skipped} skipped, {failed} failed.")


async def run() -> None:
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY not configured — skipping fun_fact batch fetch.")
        return

    db = SessionLocal()
    try:
        pending = db.query(NudgeFunFactBatch).filter(NudgeFunFactBatch.status == "submitted").all()
        if not pending:
            print("No pending fun_fact batches.")
            return

        client = Anthropic(api_key=settings.anthropic_api_key)
        for batch_row in pending:
            try:
                await _process_batch(db, batch_row, client)
            except Exception as exc:  # noqa: BLE001 — one bad batch shouldn't block the rest
                db.rollback()
                print(f"Batch {batch_row.batch_id}: error while processing — {exc}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
