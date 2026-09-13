"""
Tests for the fun_fact re-engagement nudge batch pipeline (see
app.services.nudges module docstring, scripts/submit_nudge_funfact_batch.py,
scripts/fetch_nudge_funfact_batch.py). Uses the in-memory sqlite db_session
fixture (not pg_db_session) — nothing here depends on tz-aware Postgres
comparisons, and this keeps these tests runnable without a live Postgres.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from app.models.core import Centre, Document, DocumentChunk, NudgeFunFactBatch, Student  # noqa: E402
from app.services import cost_tracker  # noqa: E402
from app.services.nudges import FUN_FACT_MAX_ATTEMPTS, NO_FACT_SENTINEL  # noqa: E402

import fetch_nudge_funfact_batch as fetch_batch  # noqa: E402
import submit_nudge_funfact_batch as submit_batch  # noqa: E402


def _make_student(db_session, **overrides):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    defaults = dict(name="Test Student", phone="919000000030", centre_id=centre.id, class_="9", board="CBSE")
    defaults.update(overrides)
    student = Student(**defaults)
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def _add_chunk(db_session, content="Water expands when it freezes.", class_="9", board="CBSE"):
    doc = Document(title="States of Matter", class_=class_, subject="Science", chapter="States of Matter", board=board)
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    db_session.add(DocumentChunk(document_id=doc.id, chunk_index=0, content=content))
    db_session.commit()


# ---------------------------------------------------------------------------
# Submit phase
# ---------------------------------------------------------------------------

def test_build_requests_skips_a_student_with_no_ingested_content(db_session, monkeypatch):
    student = _make_student(db_session)  # class 9, no chunks ingested
    monkeypatch.setattr(submit_batch, "_find_inactive_students", lambda db: [student])
    monkeypatch.setattr(submit_batch.cost_tracker, "has_credits", lambda db, sid: True)
    monkeypatch.setattr(submit_batch.school_billing, "is_centre_churned", lambda db, cid: False)

    requests, tracking = submit_batch._build_requests(db_session)
    assert requests == []
    assert tracking == []


def test_build_requests_skips_a_student_out_of_credits(db_session, monkeypatch):
    student = _make_student(db_session)
    _add_chunk(db_session)
    monkeypatch.setattr(submit_batch, "_find_inactive_students", lambda db: [student])
    monkeypatch.setattr(submit_batch.cost_tracker, "has_credits", lambda db, sid: False)
    monkeypatch.setattr(submit_batch.school_billing, "is_centre_churned", lambda db, cid: False)

    requests, tracking = submit_batch._build_requests(db_session)
    assert requests == []
    assert tracking == []


def test_build_requests_skips_a_student_not_due_for_fun_fact(db_session, monkeypatch):
    """fun_fact still on cooldown for this student — eligible_for_fun_fact
    should exclude them, same cooldown check pick_next_nudge uses."""
    from datetime import datetime, timezone

    student = _make_student(db_session)
    _add_chunk(db_session)
    student.nudges_sent = {"fun_fact": {"sent_at": datetime.now(timezone.utc).isoformat(), "detail": None}}
    db_session.commit()

    monkeypatch.setattr(submit_batch, "_find_inactive_students", lambda db: [student])
    monkeypatch.setattr(submit_batch.cost_tracker, "has_credits", lambda db, sid: True)
    monkeypatch.setattr(submit_batch.school_billing, "is_centre_churned", lambda db, cid: False)

    requests, tracking = submit_batch._build_requests(db_session)
    assert requests == []
    assert tracking == []


def test_build_requests_builds_one_request_per_candidate_chunk_with_matching_custom_ids(db_session, monkeypatch):
    student = _make_student(db_session)
    _add_chunk(db_session, content="Water expands when it freezes, making ice less dense than liquid water.")

    monkeypatch.setattr(submit_batch, "_find_inactive_students", lambda db: [student])
    monkeypatch.setattr(submit_batch.cost_tracker, "has_credits", lambda db, sid: True)
    monkeypatch.setattr(submit_batch.school_billing, "is_centre_churned", lambda db, cid: False)

    requests, tracking = submit_batch._build_requests(db_session)

    # Only one chunk was ingested, so only one (student, chunk) pair exists
    # even though FUN_FACT_MAX_ATTEMPTS allows more.
    assert len(requests) == 1
    assert len(tracking) == 1
    assert tracking[0]["student_id"] == student.id
    assert tracking[0]["chapter"] == "States of Matter"
    assert requests[0]["custom_id"] == tracking[0]["custom_id"] == "student-1-attempt-0"
    assert requests[0]["params"]["model"] == submit_batch.FUN_FACT_MODEL
    assert "Water expands" in requests[0]["params"]["messages"][0]["content"]


def test_run_creates_a_batch_and_persists_tracking_state(db_session, monkeypatch):
    student = _make_student(db_session)
    _add_chunk(db_session)

    monkeypatch.setattr(submit_batch, "_find_inactive_students", lambda db: [student])
    monkeypatch.setattr(submit_batch.cost_tracker, "has_credits", lambda db, sid: True)
    monkeypatch.setattr(submit_batch.school_billing, "is_centre_churned", lambda db, cid: False)
    monkeypatch.setattr(submit_batch, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(submit_batch.settings, "anthropic_api_key", "sk-ant-test")

    created_batch = SimpleNamespace(id="batch_abc123")

    class FakeBatches:
        def create(self, requests):
            self.received_requests = requests
            return created_batch

    fake_batches = FakeBatches()

    class FakeClient:
        def __init__(self, api_key):
            self.messages = SimpleNamespace(batches=fake_batches)

    monkeypatch.setattr(submit_batch, "Anthropic", FakeClient)
    # db_session.close() is a no-op here so later assertions can still use it
    monkeypatch.setattr(db_session, "close", lambda: None)

    submit_batch.run()

    assert len(fake_batches.received_requests) == 1
    row = db_session.query(NudgeFunFactBatch).filter_by(batch_id="batch_abc123").first()
    assert row is not None
    assert row.status == "submitted"
    assert row.requests[0]["student_id"] == student.id


def test_run_skips_the_api_call_entirely_with_nothing_eligible(db_session, monkeypatch):
    monkeypatch.setattr(submit_batch, "_find_inactive_students", lambda db: [])
    monkeypatch.setattr(submit_batch, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(submit_batch.settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(db_session, "close", lambda: None)

    def _boom(api_key):
        raise AssertionError("Anthropic client should never be constructed with nothing to submit")

    monkeypatch.setattr(submit_batch, "Anthropic", _boom)

    submit_batch.run()  # must not raise
    assert db_session.query(NudgeFunFactBatch).count() == 0


# ---------------------------------------------------------------------------
# Fetch phase
# ---------------------------------------------------------------------------

class _FakeUsage:
    def __init__(self, input_tokens=50, output_tokens=20):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0


class _FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text, model="claude-haiku-4-5-20251001"):
        self.content = [_FakeTextBlock(text)]
        self.model = model
        self.usage = _FakeUsage()


class _FakeSucceeded:
    def __init__(self, text):
        self.type = "succeeded"
        self.message = _FakeMessage(text)


class _FakeErrored:
    type = "errored"


class _FakeResult:
    def __init__(self, custom_id, result):
        self.custom_id = custom_id
        self.result = result


def _fake_client(processing_status, results):
    class FakeBatches:
        def retrieve(self, batch_id):
            return SimpleNamespace(processing_status=processing_status)

        def results(self, batch_id):
            return results

    class FakeClient:
        def __init__(self, api_key):
            self.messages = SimpleNamespace(batches=FakeBatches())

    return FakeClient


async def test_fetch_skips_an_incomplete_batch_without_sending_or_marking_processed(db_session, monkeypatch):
    student = _make_student(db_session)
    row = NudgeFunFactBatch(
        batch_id="batch_pending",
        status="submitted",
        requests=[{"custom_id": "student-1-attempt-0", "student_id": student.id, "chapter": "States of Matter"}],
    )
    db_session.add(row)
    db_session.commit()

    monkeypatch.setattr(fetch_batch, "settings", SimpleNamespace(anthropic_api_key="sk-ant-test"))
    monkeypatch.setattr(fetch_batch, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(fetch_batch, "Anthropic", _fake_client("in_progress", []))
    monkeypatch.setattr(db_session, "close", lambda: None)

    sent_calls = []

    async def fake_send(*args, **kwargs):
        sent_calls.append((args, kwargs))
        return {"sent": True}

    monkeypatch.setattr(fetch_batch, "send_template_message", fake_send)

    await fetch_batch.run()

    assert sent_calls == []
    db_session.refresh(row)
    assert row.status == "submitted"  # unchanged — safe to retry later


async def test_fetch_sends_only_for_a_succeeded_result_and_marks_batch_processed(db_session, monkeypatch):
    student = _make_student(db_session)
    row = NudgeFunFactBatch(
        batch_id="batch_done",
        status="submitted",
        requests=[{"custom_id": "student-1-attempt-0", "student_id": student.id, "chapter": "States of Matter"}],
    )
    db_session.add(row)
    db_session.commit()

    fake_result = _FakeResult("student-1-attempt-0", _FakeSucceeded("Did you know? Ice floats! 🧊"))
    monkeypatch.setattr(fetch_batch, "settings", SimpleNamespace(anthropic_api_key="sk-ant-test"))
    monkeypatch.setattr(fetch_batch, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(fetch_batch, "Anthropic", _fake_client("ended", [fake_result]))
    monkeypatch.setattr(fetch_batch.cost_tracker, "has_credits", lambda db, sid: True)
    monkeypatch.setattr(fetch_batch.school_billing, "is_centre_churned", lambda db, cid: False)
    monkeypatch.setattr(db_session, "close", lambda: None)

    sent_calls = []

    async def fake_send(phone, template_name, params):
        sent_calls.append((phone, template_name, params))
        return {"sent": True}

    monkeypatch.setattr(fetch_batch, "send_template_message", fake_send)

    recorded = []
    real_record = cost_tracker.record_platform_claude_usage

    def spy_record(db, model, input_tokens, output_tokens, student_id, **kwargs):
        recorded.append(kwargs)
        return real_record(db, model, input_tokens, output_tokens, student_id, **kwargs)

    monkeypatch.setattr(fetch_batch.cost_tracker, "record_platform_claude_usage", spy_record)

    await fetch_batch.run()

    assert len(sent_calls) == 1
    phone, template_name, params = sent_calls[0]
    assert phone == student.phone
    assert params == [{"name": "1", "value": "Did you know? Ice floats! 🧊"}]

    assert recorded == [{"cache_write_tokens": 0, "cache_read_tokens": 0, "feature": "nudge_funfact", "batch": True}]

    db_session.refresh(row)
    assert row.status == "processed"
    assert row.processed_at is not None
    assert "fun_fact" in student.nudges_sent
    assert student.nudges_sent["fun_fact"]["detail"] == "States of Matter"


async def test_fetch_skips_a_student_with_no_successful_result_no_duplicate_or_partial_send(db_session, monkeypatch):
    """Every candidate for this student came back NO_FACT or errored — no
    WhatsApp send should happen, and the batch should still be marked
    processed (nothing left to retry for it — a fresh cycle picks new
    random chunks next time)."""
    student = _make_student(db_session)
    row = NudgeFunFactBatch(
        batch_id="batch_no_fact",
        status="submitted",
        requests=[
            {"custom_id": "student-1-attempt-0", "student_id": student.id, "chapter": "Ch A"},
            {"custom_id": "student-1-attempt-1", "student_id": student.id, "chapter": "Ch B"},
        ],
    )
    db_session.add(row)
    db_session.commit()

    results = [
        _FakeResult("student-1-attempt-0", _FakeSucceeded(NO_FACT_SENTINEL)),
        _FakeResult("student-1-attempt-1", _FakeErrored()),
    ]
    monkeypatch.setattr(fetch_batch, "settings", SimpleNamespace(anthropic_api_key="sk-ant-test"))
    monkeypatch.setattr(fetch_batch, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(fetch_batch, "Anthropic", _fake_client("ended", results))
    monkeypatch.setattr(fetch_batch.cost_tracker, "has_credits", lambda db, sid: True)
    monkeypatch.setattr(fetch_batch.school_billing, "is_centre_churned", lambda db, cid: False)
    monkeypatch.setattr(db_session, "close", lambda: None)

    sent_calls = []

    async def fake_send(*args, **kwargs):
        sent_calls.append((args, kwargs))
        return {"sent": True}

    monkeypatch.setattr(fetch_batch, "send_template_message", fake_send)

    await fetch_batch.run()

    assert sent_calls == []
    db_session.refresh(row)
    assert row.status == "processed"
    assert not (student.nudges_sent or {}).get("fun_fact")


async def test_fetch_never_reprocesses_an_already_processed_batch(db_session, monkeypatch):
    student = _make_student(db_session)
    row = NudgeFunFactBatch(
        batch_id="batch_old",
        status="processed",
        requests=[{"custom_id": "student-1-attempt-0", "student_id": student.id, "chapter": "States of Matter"}],
    )
    db_session.add(row)
    db_session.commit()

    def _boom(api_key):
        raise AssertionError("should never contact Anthropic for an already-processed batch")

    monkeypatch.setattr(fetch_batch, "settings", SimpleNamespace(anthropic_api_key="sk-ant-test"))
    monkeypatch.setattr(fetch_batch, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(fetch_batch, "Anthropic", _boom)
    monkeypatch.setattr(db_session, "close", lambda: None)

    await fetch_batch.run()  # must not raise / must not touch Anthropic
