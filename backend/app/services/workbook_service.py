import json
import re

from app.services.llm_client import call_llm

WORKBOOK_MODEL = "claude-sonnet-4-6"  # a teacher-facing deliverable — worth the better tier over quiz's Haiku
MAX_QUESTIONS = 25  # keeps a single generation call (and its cost) bounded


async def generate_workbook_questions(
    topic: str, class_: str | None, num_questions: int
) -> tuple[list[dict], object]:
    """
    Returns (questions, llm_result) where each question is
    {"question": str, "answer": str} — llm_result carries token usage so
    the caller can bill the school ledger (see app.services.school_billing).
    """
    num_questions = max(1, min(num_questions, MAX_QUESTIONS))
    class_note = f" for Class {class_} students" if class_ else ""
    system_prompt = (
        f"Generate exactly {num_questions} practice questions on the topic \"{topic}\"{class_note}, "
        "suitable for a printed worksheet. Mix question types (short answer, numeric, one-line "
        "explanation) and difficulty levels. Each question needs one clear correct answer. "
        'Respond with ONLY a JSON array, no markdown fences, no explanation: '
        '[{"question": "...", "answer": "..."}, ...] — exactly '
        f"{num_questions} items."
    )
    result = await call_llm(system_prompt=system_prompt, messages=[{"role": "user", "content": topic}], model=WORKBOOK_MODEL)
    text = re.sub(r"^```(json)?|```$", "", result.text.strip(), flags=re.MULTILINE).strip()
    try:
        questions = json.loads(text)
        questions = [q for q in questions if isinstance(q, dict) and q.get("question") and q.get("answer")]
    except (json.JSONDecodeError, TypeError):
        questions = []
    return questions, result
