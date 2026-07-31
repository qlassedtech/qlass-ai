import json
import re

from app.services.llm_client import call_llm, classify

_QUIZ_START_PATTERNS = [
    r"quiz me on (.+)", r"quiz me about (.+)", r"quiz on (.+)", r"test me on (.+)",
    r"test me about (.+)", r"give me a quiz on (.+)", r"take a quiz on (.+)",
]
_QUIZ_SKIP_PHRASES = ["skip", "pass", "next question", "i don't know", "idk", "no idea"]

# "quiz on (.+)" happily captures a referential phrase like "the same" or
# "this" literally as the topic (e.g. "quiz on the same" -> "the same"),
# which would ask the LLM to generate a quiz on the literal words "the
# same" instead of resolving what topic the student actually means — the
# caller (whatsapp.py) should check this and substitute the student's most
# recently discussed topic instead. See is_vague_quiz_topic.
_VAGUE_TOPIC_PHRASES = {"the same", "same", "this", "that", "this topic", "that topic", "it"}

# A bare quiz request with no "on X"/"about X" clause at all (e.g. "give me
# the quiz", "quiz me now") — confirmed live as a real gap: this used to
# match none of _QUIZ_START_PATTERNS (all require an explicit topic
# clause), so it fell through to a normal LLM tutoring turn instead of the
# real quiz engine. The model then free-styled its own untracked,
# ungraded quiz-like text, which also collided with the router's own
# profile-question interleaving (see whatsapp.py) since that logic only
# ever backs off while a real quiz is active (Student.active_quiz_id set).
# Routing these through the vague-topic path (is_vague_quiz_topic) fixes
# both: the request now starts a real, tracked quiz on whatever topic was
# last discussed.
_QUIZ_BARE_PHRASES = ["give me the quiz", "give me a quiz", "quiz me now", "start the quiz", "start a quiz", "quiz me"]


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
    if any(phrase in lowered for phrase in _QUIZ_BARE_PHRASES):
        return "this"  # vague marker — caller resolves via is_vague_quiz_topic + last_discussed_topic
    return None


async def generate_quiz_questions(
    topic: str, student_class: str | None, num_questions: int = QUIZ_QUESTION_COUNT
) -> tuple[list[dict], object]:
    """
    Returns (questions, llm_result) where questions is a list of
    {"question": str, "answer": str, "question_type": "short_answer" |
    "multiple_choice"} — llm_result is returned too so the caller can
    record actual token usage/cost. num_questions defaults to the usual
    5-question ad-hoc quiz; mock tests (see MOCK_TEST_QUESTION_COUNT) pass
    a larger count for a fuller board-exam style practice test.

    Mixed format (both types in the same quiz) rather than short-answer-only
    — a real exam mixes formats, and multiple-choice options are embedded
    directly in "question" (e.g. "...\nA) ...\nB) ...") so no schema/display
    changes are needed elsewhere; "answer" for a multiple_choice item is
    just the correct letter, which grade_answer below is told to treat as
    equivalent to a student answering with either the letter or its text.
    """
    class_note = f" for a Class {student_class} student" if student_class else ""
    system_prompt = (
        f"Generate exactly {num_questions} quiz questions on the topic "
        f'"{topic}"{class_note}. Use a MIX of both question types below (not all one type) — cover a '
        "mix of difficulty, testing real understanding not just recall.\n\n"
        '1. "short_answer": ONE clear, short correct answer (a number, word, or short phrase — not '
        "an essay). \"question\" is just the question text.\n"
        '2. "multiple_choice": 4 options labeled A) B) C) D), embedded directly in "question" as '
        'separate lines after the question itself. "answer" is ONLY the correct letter (e.g. "B").\n\n'
        'Respond with ONLY a JSON array, no markdown fences, no explanation: '
        '[{"question": "...", "answer": "...", "question_type": "short_answer"|"multiple_choice"}, ...] '
        f"— exactly {num_questions} items."
    )
    result = await call_llm(system_prompt=system_prompt, messages=[{"role": "user", "content": topic}], model=QUIZ_MODEL)
    text = result.text.strip()
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        questions = json.loads(text)
        questions = [q for q in questions if isinstance(q, dict) and q.get("question") and q.get("answer")]
        for q in questions:
            q.setdefault("question_type", "short_answer")
    except (json.JSONDecodeError, TypeError):
        questions = []
    return questions, result


async def grade_answer(question: str, correct_answer: str, given_answer: str) -> tuple[bool, object]:
    """
    Loose equivalence check (via a cheap deterministic Haiku call) rather
    than exact string matching — a student answering "0.8" vs "0.8 N" vs
    "8 x 10^-1" for the same numeric answer should all grade as correct.
    Also handles multiple-choice: correct_answer is just a letter (e.g.
    "B"), and a student may reply with the letter, the option's full text,
    or both (e.g. "B" / "B) Face recognition" / "face recognition") — the
    question text itself carries the A)/B)/C)/D) options for this case.
    """
    system_prompt = (
        "You are grading a student's quiz answer. Given the question, the expected correct answer, "
        "and the student's answer, decide if the student's answer is correct — allow different but "
        "equivalent forms (e.g. different units, decimal vs fraction, minor wording differences). "
        "If the question is multiple-choice (options labeled A/B/C/D appear in the question text) and "
        "the expected answer is a single letter, also accept the student naming that option's text "
        "instead of (or in addition to) the letter, as long as it matches the correct option. "
        "Respond with ONLY 'yes' or 'no', nothing else."
    )
    message = f"Question: {question}\nExpected answer: {correct_answer}\nStudent's answer: {given_answer}"
    result = await classify(system_prompt, [{"role": "user", "content": message}], fallback="no", model=QUIZ_MODEL)
    return result.text.strip().lower().startswith("y"), result
