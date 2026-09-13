"""
Submit phase of the "fun_fact" re-engagement nudge batch pipeline (see
app.services.nudges module docstring). Finds every inactive student who's
due a "fun_fact" nudge today, builds one Anthropic Message Batch containing
up to FUN_FACT_MAX_ATTEMPTS candidate-chunk requests per student (mirroring
the retry-across-random-chunks behaviour of the old synchronous
app.services.nudges._generate_fun_fact — the first chunk that yields a real
fact wins), submits it in ONE call to the Batches API (~50% cheaper than
the equivalent number of synchronous calls, since none of this is
real-time), and records the batch id + per-request student/chapter mapping
in NudgeFunFactBatch so scripts/fetch_nudge_funfact_batch.py can reconcile
it once Anthropic finishes processing (typically well under an hour, SLA
up to 24h — see that script for the fetch side).

Reuses send_engagement_nudges.py's inactive-student query and
churned/credits gate verbatim (same functions, not reimplemented logic),
and app.services.nudges' cooldown check (eligible_for_fun_fact) and prompt-
building helpers (FUN_FACT_SYSTEM_PROMPT, _fun_fact_candidate_chunks,
_fun_fact_user_message) — this script only assembles those into batch
Request objects and persists tracking state; it does not duplicate any
eligibility or prompt logic.

Intended cron slot: run BEFORE scripts/fetch_nudge_funfact_batch.py, e.g.

    0 16 * * *  cd $APP && $PY scripts/submit_nudge_funfact_batch.py >> logs/cron-nudge-funfact-submit.log 2>&1

Safe to run with nothing eligible (just logs and exits) and safe to run
even if a previous batch is still pending fetch — each run is an
independent submission of that day's newly-eligible students only, since
eligible_for_fun_fact already excludes anyone whose fun_fact cooldown
hasn't elapsed (which includes anyone a still-pending batch already
covers, once it's actually sent — see the fetch script for the one nuance
here, documented there).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from anthropic import Anthropic  # noqa: E402
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming  # noqa: E402
from anthropic.types.messages.batch_create_params import Request  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.core import NudgeFunFactBatch  # noqa: E402
from app.services import cost_tracker, school_billing  # noqa: E402
from app.services.nudges import (  # noqa: E402
    FUN_FACT_MODEL,
    FUN_FACT_SYSTEM_PROMPT,
    _fun_fact_candidate_chunks,
    _fun_fact_user_message,
    eligible_for_fun_fact,
)

from send_engagement_nudges import _find_inactive_students  # noqa: E402

# Same reasoning as call_llm's system-prompt caching (see
# app.services.llm_client._cached_system): every request in this batch
# shares the exact same system prompt, so marking it cache-eligible can
# still save cost even though these are one-shot (non-conversation) calls —
# per Anthropic's own "Batch with Prompt Caching" pattern.
_CACHED_SYSTEM = [{"type": "text", "text": FUN_FACT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


def _build_requests(db) -> tuple[list, list[dict]]:
    """Returns (anthropic_requests, tracking) — tracking is what gets
    persisted on NudgeFunFactBatch.requests, one entry per anthropic
    request (so multiple entries can share a student_id — one per
    candidate chunk, exactly like _generate_fun_fact's attempt loop)."""
    students = _find_inactive_students(db)
    anthropic_requests: list = []
    tracking: list[dict] = []
    for student in students:
        # Same gate as send_engagement_nudges.py's run() — nudging someone
        # who can't act on it wastes the message either way.
        if school_billing.is_centre_churned(db, student.centre_id) and not cost_tracker.has_independent_payment(db, student.id):
            continue
        if not cost_tracker.has_credits(db, student.id):
            continue
        if not eligible_for_fun_fact(student):
            continue

        rows = _fun_fact_candidate_chunks(db, student)
        if not rows:
            continue  # no ingested content for this class/board yet
        for attempt, (content, subject, chapter) in enumerate(rows):
            custom_id = f"student-{student.id}-attempt-{attempt}"
            anthropic_requests.append(
                Request(
                    custom_id=custom_id,
                    params=MessageCreateParamsNonStreaming(
                        model=FUN_FACT_MODEL,
                        max_tokens=1024,
                        system=_CACHED_SYSTEM,
                        messages=[{"role": "user", "content": _fun_fact_user_message(subject, chapter, content)}],
                    ),
                )
            )
            tracking.append({"custom_id": custom_id, "student_id": student.id, "chapter": chapter})
    return anthropic_requests, tracking


def run() -> None:
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY not configured — skipping fun_fact batch submission.")
        return

    db = SessionLocal()
    try:
        anthropic_requests, tracking = _build_requests(db)
        if not anthropic_requests:
            print("No students eligible for a fun_fact batch today.")
            return

        client = Anthropic(api_key=settings.anthropic_api_key)
        batch = client.messages.batches.create(requests=anthropic_requests)

        db.add(NudgeFunFactBatch(batch_id=batch.id, status="submitted", requests=tracking))
        db.commit()

        num_students = len({entry["student_id"] for entry in tracking})
        print(f"Submitted batch {batch.id}: {len(anthropic_requests)} request(s) for {num_students} student(s).")
    finally:
        db.close()


if __name__ == "__main__":
    run()
