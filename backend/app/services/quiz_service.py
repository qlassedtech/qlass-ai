import json
import re

from app.services.llm_client import call_llm, classify

QUIZ_QUESTION_COUNT = 5
QUIZ_MODEL = "claude-haiku-4-5-20251001"  # narrow, well-defined tasks — cheap tier is enough

# A longer, timed board-exam style practice test rather than the usual
# 5-question ad-hoc quiz.
MOCK_TEST_QUESTION_COUNT = 15


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
    """
    class_note = f" for a Class {student_class} student" if student_class else ""
    board_note = f" following the {board} syllabus" if board else ""
    system_prompt = (
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
