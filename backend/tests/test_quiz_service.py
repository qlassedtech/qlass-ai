import json

from app.services.llm_client import LLMResult
from app.services import quiz_service


def _result(questions: list[dict]) -> LLMResult:
    return LLMResult(
        text=json.dumps(questions), model="claude-haiku-4-5-20251001",
        input_tokens=10, output_tokens=10, cache_write_tokens=0, cache_read_tokens=0,
    )


def _question(n: int) -> dict:
    return {"question": f"Q{n}?", "answer": f"A{n}", "question_type": "short_answer"}


async def test_returns_full_count_on_first_try(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model):
        return _result([_question(i) for i in range(5)])

    monkeypatch.setattr(quiz_service, "call_llm", fake_call_llm)
    questions, result = await quiz_service.generate_quiz_questions("photosynthesis", "8", num_questions=5)

    assert len(questions) == 5
    assert result.input_tokens == 10  # only one call made — no retry needed


async def test_retries_for_the_shortfall_when_under_delivered(monkeypatch):
    """
    Confirmed live: a student asked for a 5-question quiz, the model only
    returned 2, and the quiz just silently ran as a 2-question quiz with no
    attempt to make up the difference — the retry here is the actual fix,
    not just a log line noting the shortfall happened.
    """
    calls = []

    async def fake_call_llm(system_prompt, messages, model):
        calls.append(system_prompt)
        if len(calls) == 1:
            return _result([_question(1), _question(2)])  # short by 3
        return _result([_question(i) for i in range(3, 6)])  # makes up the rest

    monkeypatch.setattr(quiz_service, "call_llm", fake_call_llm)
    questions, result = await quiz_service.generate_quiz_questions("photosynthesis", "8", num_questions=5)

    assert len(questions) == 5
    assert len(calls) == 2
    assert "exactly 3 quiz questions" in calls[1]  # second call asked for just the shortfall
    assert result.input_tokens == 20  # usage from both calls combined, not just the last one


async def test_gives_up_after_max_attempts_rather_than_looping_forever(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model):
        return _result([_question(1)])  # always short, never catches up

    monkeypatch.setattr(quiz_service, "call_llm", fake_call_llm)
    questions, result = await quiz_service.generate_quiz_questions("photosynthesis", "8", num_questions=5)

    assert len(questions) == quiz_service.MAX_GENERATION_ATTEMPTS
    assert result.input_tokens == 10 * quiz_service.MAX_GENERATION_ATTEMPTS


async def test_malformed_response_is_treated_as_zero_questions_and_retried(monkeypatch):
    calls = []

    async def fake_call_llm(system_prompt, messages, model):
        calls.append(system_prompt)
        if len(calls) == 1:
            return LLMResult(text="not json at all", model="claude-haiku-4-5-20251001", input_tokens=5, output_tokens=5)
        return _result([_question(i) for i in range(5)])

    monkeypatch.setattr(quiz_service, "call_llm", fake_call_llm)
    questions, result = await quiz_service.generate_quiz_questions("photosynthesis", "8", num_questions=5)

    assert len(questions) == 5
    assert len(calls) == 2
