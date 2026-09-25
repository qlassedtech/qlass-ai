"""
Regression tests for the Sept 2026 ops-visibility audit fixes: LLMResult.ok
(failure text must never ship as content), ops alerts + provider error
counters, WhatsApp 4,096-char splitting, TTS billing clamp, and the
overdraft guard on has_credits.
"""
import anthropic
import httpx
import pytest

from app.models.core import Answer, Centre, Student, TopicProgress
from app.services import (
    alerts, cost_tracker, nudges, quiz_flow, quiz_service, rate_limit, sarvam_client, whatsapp_client,
)
from app.services.llm_client import LLMResult


# ---------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def no_redis(monkeypatch):
    """Force the in-process fallbacks so tests never need (or touch) a live Redis."""
    monkeypatch.setattr(rate_limit, "_redis", None)  # alerts' async client is rate_limit._client()
    monkeypatch.setattr(alerts, "_redis_sync", None)
    monkeypatch.setattr(alerts, "_fallback_cooldowns", {})
    monkeypatch.setattr(alerts, "_fallback_error_counts", {})


@pytest.fixture()
def sent_alerts(monkeypatch):
    """Capture what alert_ops would send instead of calling Wati."""
    sent = []

    async def fake_send(to_phone, body):
        sent.append((to_phone, body))
        return {"sent": True}

    monkeypatch.setattr(whatsapp_client, "send_whatsapp_message", fake_send)
    return sent


def _make_student(db_session, phone="919000000001"):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Test Student", phone=phone, centre_id=centre.id, class_="8")
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def _failed_result(text="Sorry, I'm having trouble reaching the AI service right now.") -> LLMResult:
    return LLMResult(text=text, model="claude-haiku-4-5-20251001", ok=False)


# ------------------------------------------------------- 1. LLMResult.ok

def test_llm_result_defaults_to_ok():
    assert LLMResult(text="hi", model="m").ok is True


async def test_call_llm_marks_anthropic_api_error_as_not_ok(monkeypatch):
    from app.services import llm_client

    class FakeMessages:
        async def create(self, **kwargs):
            raise anthropic.APIStatusError(
                "credit balance too low", response=httpx.Response(402, request=httpx.Request("POST", "https://x")), body=None,
            )

    class FakeClient:
        messages = FakeMessages()

    reported = []
    monkeypatch.setattr(llm_client, "_client", FakeClient())
    monkeypatch.setattr(llm_client, "report_provider_error", lambda provider, status: reported.append((provider, status)))

    result = await llm_client.call_llm("sys", [{"role": "user", "content": "hi"}], model="claude-sonnet-4-6")
    assert result.ok is False
    assert "trouble reaching" in result.text  # existing callers still get the apology text
    assert reported == [("anthropic", 402)]


async def test_classify_marks_anthropic_api_error_as_not_ok_but_keeps_fallback(monkeypatch):
    from app.services import llm_client

    class FakeMessages:
        async def create(self, **kwargs):
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr(llm_client, "_client", FakeClient())
    monkeypatch.setattr(llm_client, "report_provider_error", lambda provider, status: None)

    result = await llm_client.classify("sys", [{"role": "user", "content": "hi"}], fallback="no")
    assert result.ok is False
    assert result.text == "no"


async def test_fun_fact_is_not_sent_when_the_llm_call_failed(db_session, monkeypatch):
    """The apology text must never go out as a 'Did you know?' nudge."""
    student = _make_student(db_session)

    async def fake_call_llm(system_prompt, messages, model):
        return _failed_result()

    monkeypatch.setattr(nudges, "call_llm", fake_call_llm)
    monkeypatch.setattr(nudges, "_fun_fact_candidate_chunks", lambda db, s, limit=4: [("some chunk", "Science", "Ch 1")])
    assert await nudges._generate_fun_fact(db_session, student) is None


async def test_grade_answer_returns_none_when_classifier_failed(monkeypatch):
    async def fake_classify(system_prompt, messages, fallback, model):
        return LLMResult(text=fallback, model=model, ok=False)

    monkeypatch.setattr(quiz_service, "classify", fake_classify)
    is_correct, result = await quiz_service.grade_answer("Q?", "A", "B")
    assert is_correct is None
    assert result.ok is False


async def test_grade_answer_still_grades_a_real_no(monkeypatch):
    async def fake_classify(system_prompt, messages, fallback, model):
        return LLMResult(text="no", model=model)

    monkeypatch.setattr(quiz_service, "classify", fake_classify)
    is_correct, _ = await quiz_service.grade_answer("Q?", "A", "B")
    assert is_correct is False


async def test_quiz_flow_asks_to_retry_and_records_nothing_when_grading_failed(db_session, monkeypatch):
    student = _make_student(db_session)

    async def fake_generate(topic, student_class, num_questions=5, board=None):
        return [{"question": "Q0?", "answer": "A0", "question_type": "short_answer"}], LLMResult(text="", model="m")

    monkeypatch.setattr(quiz_flow, "generate_quiz_questions", fake_generate)
    await quiz_flow.start_quiz(db_session, student, "topic")

    async def fake_grade(question, correct_answer, given_answer):
        return None, _failed_result("no")

    monkeypatch.setattr(quiz_flow, "grade_answer", fake_grade)
    reply = await quiz_flow.handle_quiz_answer(db_session, student, "A0", quiz_skip=False)

    assert "couldn't check" in reply
    assert student.active_quiz_id is not None  # quiz still open on the same question
    assert db_session.query(Answer).filter(Answer.student_id == student.id).count() == 0
    assert db_session.query(TopicProgress).filter(TopicProgress.student_id == student.id).count() == 0


# -------------------------------------------------------- 2. ops alerts

async def test_alert_ops_sends_once_per_kind_within_cooldown(sent_alerts):
    assert await alerts.alert_ops("anthropic_402", "credit balance too low") is True
    assert await alerts.alert_ops("anthropic_402", "credit balance too low") is False
    assert await alerts.alert_ops("sarvam_402", "out of credits") is True  # a different kind is not deduped
    assert len(sent_alerts) == 2
    assert sent_alerts[0][0] == alerts.ops_alert_phone()
    assert "anthropic_402" in sent_alerts[0][1]


async def test_alert_ops_never_raises(monkeypatch):
    async def boom(to_phone, body):
        raise RuntimeError("wati exploded")

    monkeypatch.setattr(whatsapp_client, "send_whatsapp_message", boom)
    assert await alerts.alert_ops("x", "y") is False


def test_note_provider_error_counts_per_provider_per_hour():
    alerts.note_provider_error("anthropic", 500)
    alerts.note_provider_error("anthropic", 500)
    alerts.note_provider_error("wati", 401)
    assert alerts.get_provider_error_count("anthropic") == 2
    assert alerts.get_provider_error_count("wati") == 1
    assert alerts.get_provider_error_count("sarvam") == 0


async def test_report_provider_error_alerts_immediately_for_402(sent_alerts):
    alerts.report_provider_error("anthropic", 402)
    await _drain_background_tasks()
    assert len(sent_alerts) == 1
    body = sent_alerts[0][1]
    assert "402" in body and "credit balance" in body and "console.anthropic.com" in body
    assert alerts.get_provider_error_count("anthropic") == 1


async def test_report_provider_error_only_counts_a_400(sent_alerts):
    alerts.report_provider_error("anthropic", 400)
    await _drain_background_tasks()
    assert sent_alerts == []
    assert alerts.get_provider_error_count("anthropic") == 1


def test_report_provider_error_is_safe_outside_an_event_loop():
    alerts.report_provider_error("sarvam", 402)  # no running loop — counts, doesn't crash
    assert alerts.get_provider_error_count("sarvam") == 1


async def test_wati_401_is_counted_and_alerted(monkeypatch, sent_alerts):
    request = httpx.Request("POST", "https://wati.example/api/v1/sendSessionFile/919")

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return httpx.Response(401, request=request, text="token expired")

    monkeypatch.setattr(whatsapp_client.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(whatsapp_client.settings, "whatsapp_token", "t")
    monkeypatch.setattr(whatsapp_client.settings, "wati_api_endpoint", "https://wati.example")

    result = await whatsapp_client.send_whatsapp_image("919", b"png", "caption")
    assert result["sent"] is False
    await _drain_background_tasks()
    assert alerts.get_provider_error_count("wati") == 1
    # The alert itself goes through the (monkeypatched) session sender, not the broken image path.
    assert len(sent_alerts) == 1 and "401" in sent_alerts[0][1]


async def _drain_background_tasks():
    import asyncio

    while alerts._background_tasks:
        await asyncio.gather(*list(alerts._background_tasks), return_exceptions=True)


# ------------------------------------------- 4. WhatsApp 4,096-char limit

def _long_body(at_least: int) -> str:
    """Whole paragraphs (never cut mid-sentence) totalling at least `at_least` chars — ~9,100 for 9,000."""
    paragraph = ("This is a sentence of textbook explanation that a tutor wrote. " * 3).strip()
    paragraphs = []
    while sum(len(p) + 2 for p in paragraphs) < at_least:
        paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


async def test_long_message_is_split_into_ordered_parts(monkeypatch):
    calls = []

    async def fake_post(to_phone, body):
        calls.append((to_phone, body))
        return {"sent": True, "response": {}}

    monkeypatch.setattr(whatsapp_client, "_post_session_message", fake_post)
    body = _long_body(9000)
    result = await whatsapp_client.send_whatsapp_message("919000000001", body)

    assert result["sent"] is True
    assert result["parts_sent"] == 3
    assert len(calls) == 3
    assert all(len(part) <= whatsapp_client.WHATSAPP_TEXT_LIMIT for _, part in calls)
    # Order preserved and nothing lost: the parts re-join into the original text.
    rejoined = "\n\n".join(part for _, part in calls)
    assert rejoined.replace("\n", " ").split() == body.replace("\n", " ").split()
    for _, part in calls:
        assert part.rstrip().endswith(".")  # split on a paragraph boundary, not mid-sentence


async def test_short_message_is_sent_as_a_single_call(monkeypatch):
    calls = []

    async def fake_post(to_phone, body):
        calls.append(body)
        return {"sent": True, "response": {}}

    monkeypatch.setattr(whatsapp_client, "_post_session_message", fake_post)
    result = await whatsapp_client.send_whatsapp_message("919", "hello")
    assert calls == ["hello"]
    assert "parts_sent" not in result


async def test_split_send_reports_failure_if_any_part_fails(monkeypatch):
    calls = []

    async def fake_post(to_phone, body):
        calls.append(body)
        return {"sent": len(calls) < 2, "reason": "Wati API error 500"}

    monkeypatch.setattr(whatsapp_client, "_post_session_message", fake_post)
    result = await whatsapp_client.send_whatsapp_message("919", _long_body(9000))
    assert result["sent"] is False
    assert result["parts_sent"] == 1
    assert len(calls) == 2  # stopped after the failed part rather than sending the tail out of order


def test_split_message_falls_back_to_sentences_for_one_huge_paragraph():
    body = "A short sentence here. " * 400  # ~9,200 chars, no paragraph breaks
    parts = whatsapp_client.split_message(body)
    assert len(parts) == 3
    assert all(len(p) <= whatsapp_client.WHATSAPP_TEXT_LIMIT for p in parts)
    assert all(p.endswith(".") for p in parts)


# ------------------------------------------------------- 5. TTS billing

def test_billable_tts_chars_is_clamped_to_the_api_limit():
    assert sarvam_client.billable_tts_chars("x" * 10) == 10
    assert sarvam_client.billable_tts_chars("x" * 10_000) == sarvam_client.TTS_CHAR_LIMIT


def test_record_char_usage_bills_tts_only_for_what_sarvam_synthesises(db_session):
    student = _make_student(db_session)
    cost_tracker.add_credits(db_session, student.id, 100.0)
    balance = cost_tracker.record_char_usage(db_session, "sarvam_tts", 10_000, student.id)
    expected_raw = sarvam_client.TTS_CHAR_LIMIT * cost_tracker.PRICING["sarvam_tts"]["per_char"]
    assert balance == pytest.approx(100.0 - expected_raw * cost_tracker.MARKUP_MULTIPLIER)


def test_record_char_usage_does_not_clamp_translation(db_session):
    student = _make_student(db_session)
    cost_tracker.add_credits(db_session, student.id, 100.0)
    balance = cost_tracker.record_char_usage(db_session, "sarvam_translate", 10_000, student.id)
    expected_raw = 10_000 * cost_tracker.PRICING["sarvam_translate"]["per_char"]
    assert balance == pytest.approx(100.0 - expected_raw * cost_tracker.MARKUP_MULTIPLIER)


# ---------------------------------------------------- 6. overdraft guard

def test_has_credits_rejects_a_balance_below_one_turns_cost(db_session):
    """₹1 used to pass (balance > 0) and then absorb ₹5-9 of deductions — the −₹8.32 wallet."""
    student = _make_student(db_session)
    cost_tracker.add_credits(db_session, student.id, 1.0)
    assert cost_tracker.has_credits(db_session, student.id) is False


def test_has_credits_accepts_exactly_the_minimum(db_session):
    student = _make_student(db_session)
    cost_tracker.add_credits(db_session, student.id, cost_tracker.MIN_TURN_BALANCE_INR)
    assert cost_tracker.has_credits(db_session, student.id) is True


def test_has_credits_allow_low_accepts_any_positive_balance(db_session):
    student = _make_student(db_session)
    cost_tracker.add_credits(db_session, student.id, 1.0)
    assert cost_tracker.has_credits(db_session, student.id, allow_low=True) is True


def test_has_credits_allow_low_still_rejects_zero(db_session):
    student = _make_student(db_session)
    assert cost_tracker.has_credits(db_session, student.id, allow_low=True) is False


def test_unlimited_over_cap_top_up_below_minimum_is_rejected(db_session):
    from datetime import datetime, timedelta, timezone

    student = _make_student(db_session)
    student.subscription_plan = "unlimited"
    student.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    db_session.commit()
    weekly_cap = cost_tracker.UNLIMITED_PERIOD_SPEND_CAPS["student"]["week"]
    cost_tracker._deduct(db_session, "claude_sonnet", weekly_cap / cost_tracker.MARKUP_MULTIPLIER + 0.001, student.id)

    cost_tracker.add_credits(db_session, student.id, 1.0, note="tiny top-up")
    assert cost_tracker.has_credits(db_session, student.id) is False
    assert cost_tracker.has_credits(db_session, student.id, allow_low=True) is True
