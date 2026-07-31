import asyncio

from app.services.intent_classifier import INTENTS, classify_intent, parse_intent
from app.services.llm_client import LLMResult


def test_parse_intent_normalizes_case_and_whitespace():
    assert parse_intent(" Progress \n") == "progress"
    assert parse_intent("MENU") == "menu"


def test_parse_intent_falls_back_to_other_for_an_unrecognized_label():
    # A model slip (typo, extra words) must never propagate an unknown
    # label into the caller's `intent == "..."` checks — always normalize
    # to the safe default instead.
    assert parse_intent("progresss") == "other"
    assert parse_intent("") == "other"
    assert parse_intent("sure, progress") == "other"


def test_every_intent_label_is_in_the_closed_set():
    assert set(INTENTS) == {"menu", "progress", "referral", "teacher_help", "quiz_stop", "other"}


def test_classify_intent_forwards_the_models_raw_answer(monkeypatch):
    async def fake_classify(system_prompt, messages, fallback, model):
        assert messages == [{"role": "user", "content": "what is my performance"}]
        return LLMResult(text="progress", model=model, input_tokens=12, output_tokens=1)

    monkeypatch.setattr("app.services.intent_classifier.classify", fake_classify)

    result = asyncio.run(classify_intent("what is my performance"))

    assert parse_intent(result.text) == "progress"
    assert result.input_tokens == 12
