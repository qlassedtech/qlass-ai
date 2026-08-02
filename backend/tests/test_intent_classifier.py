import asyncio

from app.services.intent_classifier import INTENTS, classify_intent
from app.services.llm_client import LLMResult


def test_every_intent_label_is_in_the_closed_set():
    assert set(INTENTS) == {"menu", "progress", "referral", "teacher_help", "quiz_stop", "other"}


def _fake_classify(raw_text):
    async def fake(system_prompt, messages, fallback, model, max_tokens=10):
        return LLMResult(text=raw_text, model=model, input_tokens=50, output_tokens=20)
    return fake


def test_classify_intent_parses_basic_label(monkeypatch):
    monkeypatch.setattr(
        "app.services.intent_classifier.classify",
        _fake_classify(
            '[[CLASSIFY intent=progress wants_quiz=no quiz_topic=NONE '
            'wants_mock_test=no mock_test_topic=NONE quiz_skip=no]]'
        ),
    )
    result = asyncio.run(classify_intent("what is my performance"))
    assert result.intent == "progress"
    assert result.quiz_topic is None
    assert result.wants_mock_test is False
    assert result.llm_result.input_tokens == 50


def test_classify_intent_falls_back_to_other_for_unrecognized_label(monkeypatch):
    # A model slip (typo, extra words) must never propagate an unknown
    # label into the caller's `intent == "..."` checks — always normalize
    # to the safe default instead.
    monkeypatch.setattr(
        "app.services.intent_classifier.classify",
        _fake_classify('[[CLASSIFY intent=progresss wants_quiz=no quiz_topic=NONE wants_mock_test=no mock_test_topic=NONE quiz_skip=no]]'),
    )
    result = asyncio.run(classify_intent("something ambiguous"))
    assert result.intent == "other"


def test_classify_intent_extracts_quiz_topic(monkeypatch):
    monkeypatch.setattr(
        "app.services.intent_classifier.classify",
        _fake_classify(
            '[[CLASSIFY intent=other wants_quiz=yes quiz_topic="circular motion" '
            'wants_mock_test=no mock_test_topic=NONE quiz_skip=no]]'
        ),
    )
    result = asyncio.run(classify_intent("quiz me on circular motion"))
    assert result.quiz_topic == "circular motion"


def test_classify_intent_resolves_vague_quiz_reference_via_last_topic(monkeypatch):
    # Replaces the old is_vague_quiz_topic + Python fallback — the model is
    # given last_discussed_topic as context and asked to resolve "the same"
    # itself, directly in its structured output.
    async def fake(system_prompt, messages, fallback, model, max_tokens=10):
        assert "circular motion" in messages[0]["content"]  # last-topic context was passed through
        return LLMResult(
            text='[[CLASSIFY intent=other wants_quiz=yes quiz_topic="circular motion" '
            'wants_mock_test=no mock_test_topic=NONE quiz_skip=no]]',
            model=model,
        )

    monkeypatch.setattr("app.services.intent_classifier.classify", fake)
    result = asyncio.run(classify_intent("quiz me on the same", last_discussed_topic="circular motion"))
    assert result.quiz_topic == "circular motion"


def test_classify_intent_quiz_topic_none_when_not_wanting_a_quiz(monkeypatch):
    # Even if quiz_topic looks populated, wants_quiz=no must win.
    monkeypatch.setattr(
        "app.services.intent_classifier.classify",
        _fake_classify(
            '[[CLASSIFY intent=other wants_quiz=no quiz_topic="circular motion" '
            'wants_mock_test=no mock_test_topic=NONE quiz_skip=no]]'
        ),
    )
    result = asyncio.run(classify_intent("what is circular motion"))
    assert result.quiz_topic is None


def test_classify_intent_bare_mock_test_request_has_no_topic(monkeypatch):
    monkeypatch.setattr(
        "app.services.intent_classifier.classify",
        _fake_classify(
            '[[CLASSIFY intent=other wants_quiz=no quiz_topic=NONE '
            'wants_mock_test=yes mock_test_topic=NONE quiz_skip=no]]'
        ),
    )
    result = asyncio.run(classify_intent("give me a mock test"))
    assert result.wants_mock_test is True
    assert result.mock_test_topic is None


def test_classify_intent_quiz_skip(monkeypatch):
    monkeypatch.setattr(
        "app.services.intent_classifier.classify",
        _fake_classify(
            '[[CLASSIFY intent=other wants_quiz=no quiz_topic=NONE '
            'wants_mock_test=no mock_test_topic=NONE quiz_skip=yes]]'
        ),
    )
    result = asyncio.run(classify_intent("skip"))
    assert result.quiz_skip is True


def test_classify_intent_malformed_response_falls_back_safely(monkeypatch):
    monkeypatch.setattr(
        "app.services.intent_classifier.classify",
        _fake_classify("Sorry, I don't understand the format."),
    )
    result = asyncio.run(classify_intent("anything"))
    assert result.intent == "other"
    assert result.quiz_topic is None
    assert result.wants_mock_test is False
    assert result.quiz_skip is False
    assert result.relevant_excerpts == []


def test_classify_intent_extracts_relevant_excerpt_numbers(monkeypatch):
    monkeypatch.setattr(
        "app.services.intent_classifier.classify",
        _fake_classify(
            '[[CLASSIFY intent=other wants_quiz=no quiz_topic=NONE wants_mock_test=no '
            'mock_test_topic=NONE quiz_skip=no relevant_excerpts=1,3]]'
        ),
    )
    result = asyncio.run(classify_intent("what is euclid's division algorithm"))
    assert result.relevant_excerpts == [1, 3]


def test_classify_intent_relevant_excerpts_none_by_default(monkeypatch):
    monkeypatch.setattr(
        "app.services.intent_classifier.classify",
        _fake_classify(
            '[[CLASSIFY intent=other wants_quiz=no quiz_topic=NONE wants_mock_test=no '
            'mock_test_topic=NONE quiz_skip=no relevant_excerpts=NONE]]'
        ),
    )
    result = asyncio.run(classify_intent("thanks!"))
    assert result.relevant_excerpts == []


def test_classify_intent_passes_candidate_excerpts_into_prompt(monkeypatch):
    from app.services.retrieval import RetrievedChunk

    async def fake(system_prompt, messages, fallback, model, max_tokens=10):
        assert "Real Numbers" in messages[0]["content"]
        return LLMResult(text="[[CLASSIFY intent=other wants_quiz=no quiz_topic=NONE "
                               "wants_mock_test=no mock_test_topic=NONE quiz_skip=no "
                               "relevant_excerpts=1]]", model=model)

    monkeypatch.setattr("app.services.intent_classifier.classify", fake)
    candidate = RetrievedChunk(content="text", class_="10", subject="Math", chapter="Real Numbers", board="CBSE")
    result = asyncio.run(classify_intent("euclid's division algorithm", candidate_chunks=[candidate]))
    assert result.relevant_excerpts == [1]
