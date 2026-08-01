import json

from app.services.llm_client import LLMResult, call_llm

_STUDENT_EXTRACTION_PROMPT = (
    "You are extracting a student roster from OCR'd text of a school register/list photo. "
    "The text may be messy, have OCR errors, or be a table that got flattened into plain text. "
    "Extract every student row you can confidently identify. For each, output these exact keys: "
    'name, phone, class, board, school. Use null for any field not present — never invent a value. '
    "phone should be digits only. Respond with ONLY a JSON array of objects, nothing else — no prose, "
    "no explanation, no markdown code fences, just the raw JSON array, e.g. "
    '[{"name": "Aman Kumar", "phone": "9199...", "class": "10", "board": "BSEB", "school": null}]. '
    "Skip any row where you can't even confidently determine a name."
)

_TEACHER_EXTRACTION_PROMPT = (
    "You are extracting a staff roster from OCR'd text of a school staff list photo. The text may be "
    "messy or have OCR errors. Extract every staff member row you can confidently identify. For each, "
    'output these exact keys: name, phone, role. role must be "admin" for a headmaster/principal/'
    'vice-principal/vice principal, or "teacher" for everyone else. Use null for phone if not present — '
    "never invent one. Respond with ONLY a JSON array of objects, nothing else — no prose, no "
    'explanation, no markdown code fences, just the raw JSON array, e.g. '
    '[{"name": "Sunita Devi", "phone": "9199...", "role": "teacher"}]. Skip any row where you can\'t even '
    "confidently determine a name."
)


async def _extract_rows(system_prompt: str, ocr_text: str) -> tuple[list[dict], LLMResult]:
    result = await call_llm(system_prompt=system_prompt, messages=[{"role": "user", "content": ocr_text}])
    try:
        rows = json.loads(result.text)
    except (json.JSONDecodeError, TypeError):
        rows = []
    return (rows if isinstance(rows, list) else []), result


async def extract_student_rows(ocr_text: str) -> tuple[list[dict], LLMResult]:
    """Turns OCR'd roster-photo text into row dicts matching the CSV bulk-upload shape."""
    return await _extract_rows(_STUDENT_EXTRACTION_PROMPT, ocr_text)


async def extract_teacher_rows(ocr_text: str) -> tuple[list[dict], LLMResult]:
    """Turns OCR'd staff-list-photo text into row dicts matching the CSV bulk-upload shape."""
    return await _extract_rows(_TEACHER_EXTRACTION_PROMPT, ocr_text)
