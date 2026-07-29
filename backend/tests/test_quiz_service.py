from app.services.quiz_service import (
    extract_mock_test_topic,
    extract_quiz_topic,
    is_vague_quiz_topic,
    looks_like_mock_test_request,
    looks_like_quiz_skip,
    looks_like_quiz_stop,
)


def test_extract_quiz_topic_matches_common_phrasings():
    assert extract_quiz_topic("quiz me on circular motion") == "circular motion"
    assert extract_quiz_topic("test me on s block elements") == "s block elements"
    assert extract_quiz_topic("give me a quiz on photosynthesis") == "photosynthesis"


def test_extract_quiz_topic_captures_vague_referent_literally():
    """
    Regression context for a real production issue: "quiz on the same"
    captures "the same" literally as the topic — the caller (whatsapp.py)
    is responsible for resolving it via is_vague_quiz_topic, not this
    function, since it has no access to the student's conversation history.
    """
    assert extract_quiz_topic("Can we have a quiz on the same") == "the same"
    assert is_vague_quiz_topic("the same") is True
    assert is_vague_quiz_topic("circular motion") is False


def test_extract_quiz_topic_returns_none_for_unrelated_text():
    assert extract_quiz_topic("what is centripetal force") is None


def test_looks_like_quiz_stop():
    assert looks_like_quiz_stop("stop quiz please") is True
    assert looks_like_quiz_stop("quiz me on gravity") is False


def test_looks_like_quiz_skip():
    assert looks_like_quiz_skip("skip") is True
    assert looks_like_quiz_skip("i don't know") is True
    assert looks_like_quiz_skip("42") is False


def test_looks_like_mock_test_request():
    assert looks_like_mock_test_request("mock test") is True
    assert looks_like_mock_test_request("give me a board exam practice") is False  # bare phrase must match exactly
    assert looks_like_mock_test_request("mock test on circular motion") is True
    assert looks_like_mock_test_request("what is photosynthesis") is False


def test_extract_mock_test_topic():
    assert extract_mock_test_topic("mock test on circular motion") == "circular motion"
    assert extract_mock_test_topic("mock test") is None  # bare request — caller falls back to a general review
