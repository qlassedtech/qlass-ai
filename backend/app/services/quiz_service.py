import json
import re

from app.services.llm_client import call_llm, classify

_QUIZ_START_PATTERNS = [
    r"quiz me on (.+)", r"quiz me about (.+)", r"quiz on (.+)", r"test me on (.+)",
    r"test me about (.+)", r"give me a quiz on (.+)", r"take a quiz on (.+)",
]
_QUIZ_STOP_PHRASES = ["stop quiz", "cancel quiz", "end quiz", "quit quiz", "exit quiz"]
_QUIZ_SKIP_PHRASES = ["skip", "pass", "next question", "i don't know", "idk", "no idea"]

# "quiz on (.+)" happily captures a referential phrase like "the same" or
# "this" literally as the topic (e.g. "quiz on the same" -> "the same"),
# which would ask the LLM to generate a quiz on the literal words "the
# same" instead of resolving what topic the student actually means — the
# caller (whatsapp.py) should check this and substitute the student's most
# recently discussed topic instead. See is_vague_quiz_topic.
_VAGUE_TOPIC_PHRASES = {"the same", "same", "this", "that", "this topic", "that topic", "it"}


def is_vague_quiz_topic(topic: str) -> bool:
    return topic.strip().lower() in _VAGUE_TOPIC_PHRASES

QUIZ_QUESTION_COUNT = 5
QUIZ_MODEL = "claude-haiku-4-5-20251001"  # narrow, well-defined tasks — cheap tier is enough

# A longer, timed board-exam style practice test rather than the usual
# 5-question ad-hoc quiz — see is_vague_quiz_topic's sibling functions below.
MOCK_TEST_QUESTION_COUNT = 15

_MOCK_TEST_PATTERNS = [
    r"mock test on (.+)", r"mock exam on (.+)", r"board exam practice on (.+)", r"practice test on (.+)",
]
# Matched with no topic captured — a bare "mock test" means "test me on
# everything we've covered," not a specific chapter.
_MOCK_TEST_BARE_PHRASES = {
    "mock test", "mock exam", "board exam practice", "practice test", "full test", "full mock test",
}


def looks_like_mock_test_request(text: str) -> bool:
    lowered = text.lower().strip(" .!?")
    if lowered in _MOCK_TEST_BARE_PHRASES:
        return True
    return any(re.search(pattern, lowered) for pattern in _MOCK_TEST_PATTERNS)


def extract_mock_test_topic(text: str) -> str | None:
    """Returns the specific topic requested, or None for a general/all-topics mock test."""
    lowered = text.lower().strip()
    for pattern in _MOCK_TEST_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            topic = match.group(1).strip(" .!?")
            if topic:
                return topic
    return None


def looks_like_quiz_skip(text: str) -> bool:
    lowered = text.lower().strip(" .!?")
    return lowered in _QUIZ_SKIP_PHRASES or any(lowered.startswith(p) for p in _QUIZ_SKIP_PHRASES)


def extract_quiz_topic(text: str) -> str | None:
    lowered = text.lower().strip()
    for pattern in _QUIZ_START_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            topic = match.group(1).strip(" .!?")
            if topic:
                return topic
    return None


def looks_like_quiz_stop(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _QUIZ_STOP_PHRASES)


async def generate_quiz_questions(
    topic: str, student_class: str | None, num_questions: int = QUIZ_QUESTION_COUNT
) -> tuple[list[dict], object]:
    """
    Returns (questions, llm_result) where questions is a list of
    {"question": str, "answer": str} — llm_result is returned too so the
    caller can record actual token usage/cost. num_questions defaults to
    the usual 5-question ad-hoc quiz; mock tests (see MOCK_TEST_QUESTION_COUNT)
    pass a larger count for a fuller board-exam style practice test.
    """
    class_note = f" for a Class {student_class} student" if student_class else ""
    system_prompt = (
        f"Generate exactly {num_questions} short-answer quiz questions on the topic "
        f'"{topic}"{class_note}. Cover a mix of difficulty, testing real understanding not just '
        "recall. Each question should have ONE clear, short correct answer (a number, word, or "
        "short phrase — not an essay). "
        'Respond with ONLY a JSON array, no markdown fences, no explanation: '
        '[{"question": "...", "answer": "..."}, ...] — exactly '
        f"{num_questions} items."
    )
    result = await call_llm(system_prompt=system_prompt, messages=[{"role": "user", "content": topic}], model=QUIZ_MODEL)
    text = result.text.strip()
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        questions = json.loads(text)
        questions = [q for q in questions if isinstance(q, dict) and q.get("question") and q.get("answer")]
    except (json.JSONDecodeError, TypeError):
        questions = []
    return questions, result


async def grade_answer(question: str, correct_answer: str, given_answer: str) -> tuple[bool, object]:
    """
    Loose equivalence check (via a cheap deterministic Haiku call) rather
    than exact string matching — a student answering "0.8" vs "0.8 N" vs
    "8 x 10^-1" for the same numeric answer should all grade as correct.
    """
    system_prompt = (
        "You are grading a student's quiz answer. Given the question, the expected correct answer, "
        "and the student's answer, decide if the student's answer is correct — allow different but "
        "equivalent forms (e.g. different units, decimal vs fraction, minor wording differences). "
        "Respond with ONLY 'yes' or 'no', nothing else."
    )
    message = f"Question: {question}\nExpected answer: {correct_answer}\nStudent's answer: {given_answer}"
    result = await classify(system_prompt, [{"role": "user", "content": message}], fallback="no", model=QUIZ_MODEL)
    return result.text.strip().lower().startswith("y"), result
