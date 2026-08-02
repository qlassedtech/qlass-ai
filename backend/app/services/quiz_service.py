import json
import logging
import re

from app.services.llm_client import call_llm, classify, LLMResult

logger = logging.getLogger(__name__)

QUIZ_QUESTION_COUNT = 5
QUIZ_MODEL = "claude-haiku-4-5-20251001"  # narrow, well-defined tasks — cheap tier is enough

# A longer, timed board-exam style practice test rather than the usual
# 5-question ad-hoc quiz.
MOCK_TEST_QUESTION_COUNT = 15

# Bounds how many extra generation calls a single quiz request can trigger
# if the model keeps under-delivering — a quiz ending up 1-2 short of the
# request isn't worth chasing forever, but a bare single attempt with no
# retry at all meant a shortfall just silently shipped a shorter quiz with
# no real attempt to fix it (confirmed live).
MAX_GENERATION_ATTEMPTS = 3


def _build_system_prompt(topic: str, student_class: str | None, board: str | None, num_questions: int) -> str:
    class_note = f" for a Class {student_class} student" if student_class else ""
    board_note = f" following the {board} syllabus" if board else ""
    return (
        f"Generate exactly {num_questions} quiz questions on the topic "
        f'"{topic}"{class_note}{board_note}. Use a MIX of both question types below (not all one type) — cover a '
        "mix of difficulty, testing real understanding not just recall.\n\n"
        '1. "short_answer": ONE clear, short correct answer (a number, word, or short phrase — not '
        "an essay). \"question\" is just the question text.\n"
        '2. "multiple_choice": 4 options labeled A) B) C) D), embedded directly in "question" as '
        'separate lines after the question itself. "answer" is ONLY the correct letter (e.g. "B").\n\n'
        'Respond with ONLY a JSON array, no markdown fences, no explanation: '
        '[{"question": "...", "answer": "...", "question_type": "short_answer"|"multiple_choice"}, ...] '
        f"— exactly {num_questions} items."
    )


def _parse_questions(raw_text: str) -> list[dict]:
    text = re.sub(r"^```(json)?|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
    try:
        questions = json.loads(text)
        questions = [q for q in questions if isinstance(q, dict) and q.get("question") and q.get("answer")]
        for q in questions:
            q.setdefault("question_type", "short_answer")
    except (json.JSONDecodeError, TypeError):
        questions = []
    return questions


def _combine_usage(results: list[LLMResult]) -> LLMResult:
    """Sums token usage across retries into one LLMResult so callers keep billing a single object, unchanged."""
    return LLMResult(
        text=results[-1].text,
        model=results[-1].model,
        input_tokens=sum(r.input_tokens for r in results),
        output_tokens=sum(r.output_tokens for r in results),
        cache_write_tokens=sum(r.cache_write_tokens for r in results),
        cache_read_tokens=sum(r.cache_read_tokens for r in results),
    )


async def generate_quiz_questions(
    topic: str, student_class: str | None, num_questions: int = QUIZ_QUESTION_COUNT, board: str | None = None,
) -> tuple[list[dict], object]:
    """
    Returns (questions, llm_result) where questions is a list of
    {"question": str, "answer": str, "question_type": "short_answer" |
    "multiple_choice"} — llm_result is returned too so the caller can
    record actual token usage/cost. num_questions defaults to the usual
    5-question ad-hoc quiz; mock tests (see MOCK_TEST_QUESTION_COUNT) pass
    a larger count for a fuller board-exam style practice test.

    board matters for real content correctness, not just cosmetics — the
    same topic name can carry a different depth/scope/terminology under a
    different board's own syllabus (e.g. BSEB vs CBSE/NCERT), so passing it
    lets the model calibrate difficulty and phrasing instead of guessing a
    generic default.

    Mixed format (both types in the same quiz) rather than short-answer-only
    — a real exam mixes formats, and multiple-choice options are embedded
    directly in "question" (e.g. "...\nA) ...\nB) ...") so no schema/display
    changes are needed elsewhere; "answer" for a multiple_choice item is
    just the correct letter, which grade_answer below is told to treat as
    equivalent to a student answering with either the letter or its text.

    If a generation call returns fewer questions than requested (a parse
    hiccup, or the model simply under-delivering), this retries for just
    the shortfall rather than silently shipping a shorter quiz — a student
    who asked for a 5-question quiz and got 2 previously had no idea the
    quiz "ending" after 2 was actually a generation shortfall, not the real
    end of the quiz.
    """
    questions: list[dict] = []
    results: list[LLMResult] = []
    for attempt in range(MAX_GENERATION_ATTEMPTS):
        remaining = num_questions - len(questions)
        if remaining <= 0:
            break
        system_prompt = _build_system_prompt(topic, student_class, board, remaining)
        result = await call_llm(system_prompt=system_prompt, messages=[{"role": "user", "content": topic}], model=QUIZ_MODEL)
        results.append(result)
        questions.extend(_parse_questions(result.text))
        if len(questions) >= num_questions:
            break
        logger.warning(
            "generate_quiz_questions attempt %d/%d returned %d/%d questions so far for "
            "topic=%r class=%r board=%r — retrying for the shortfall",
            attempt + 1, MAX_GENERATION_ATTEMPTS, len(questions), num_questions, topic, student_class, board,
        )

    questions = questions[:num_questions]
    if len(questions) < num_questions:
        logger.warning(
            "generate_quiz_questions gave up with %d/%d questions after %d attempts for "
            "topic=%r class=%r board=%r",
            len(questions), num_questions, MAX_GENERATION_ATTEMPTS, topic, student_class, board,
        )
    return questions, _combine_usage(results)


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
