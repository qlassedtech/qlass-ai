import json
import re

from app.services.llm_client import call_llm, classify

_QUIZ_START_PATTERNS = [
    r"quiz me on (.+)", r"quiz me about (.+)", r"quiz on (.+)", r"test me on (.+)",
    r"test me about (.+)", r"give me a quiz on (.+)", r"take a quiz on (.+)",
]
_QUIZ_STOP_PHRASES = ["stop quiz", "cancel quiz", "end quiz", "quit quiz", "exit quiz"]

QUIZ_QUESTION_COUNT = 5
QUIZ_MODEL = "claude-haiku-4-5-20251001"  # narrow, well-defined tasks — cheap tier is enough


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


async def generate_quiz_questions(topic: str, student_class: str | None) -> tuple[list[dict], object]:
    """
    Returns (questions, llm_result) where questions is a list of
    {"question": str, "answer": str} — llm_result is returned too so the
    caller can record actual token usage/cost.
    """
    class_note = f" for a Class {student_class} student" if student_class else ""
    system_prompt = (
        f"Generate exactly {QUIZ_QUESTION_COUNT} short-answer quiz questions on the topic "
        f'"{topic}"{class_note}. Cover a mix of difficulty, testing real understanding not just '
        "recall. Each question should have ONE clear, short correct answer (a number, word, or "
        "short phrase — not an essay). "
        'Respond with ONLY a JSON array, no markdown fences, no explanation: '
        '[{"question": "...", "answer": "..."}, ...] — exactly '
        f"{QUIZ_QUESTION_COUNT} items."
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
