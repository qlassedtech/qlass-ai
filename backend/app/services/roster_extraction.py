import json

from app.services.llm_client import LLMResult, call_llm

_STUDENT_EXTRACTION_PROMPT = (
    "You are extracting a student roster from text that could be OCR'd from a school register/list "
    "photo, OR raw CSV/spreadsheet text exported from a school's own system with its own column names "
    "and order (e.g. \"Student Name\", \"Mobile No\", \"Grade\") rather than ours. "
    "The text may be messy, have OCR errors, or be a table that got flattened into plain text. "
    "Extract every student row you can confidently identify. Capture EVERY field below that's actually "
    "present in the source, even if it's not in every row or the column header doesn't match these "
    "names exactly (e.g. \"Guardian Contact\" or \"Father's Number\" is parent_phone; \"Sex\" is gender) "
    "— never drop real data the source contains just because a field isn't in this list's exact wording. "
    "For each row, output these exact keys: name, phone, class, board, school, email, gender, "
    "parent_name, parent_phone. gender must be \"male\" or \"female\" if determinable, else null. Use "
    "null for any field not present — never invent a value. phone and parent_phone should be digits "
    "only. Respond with ONLY a JSON array of objects, nothing else — no prose, no explanation, no "
    "markdown code fences, just the raw JSON array, e.g. "
    '[{"name": "Aman Kumar", "phone": "9199...", "class": "10", "board": "BSEB", "school": null, '
    '"email": null, "gender": "male", "parent_name": "Ramesh Kumar", "parent_phone": "9188..."}]. '
    "Skip any row where you can't even confidently determine a name."
)

_TEACHER_EXTRACTION_PROMPT = (
    "You are extracting a staff roster from text that could be OCR'd from a school staff list photo, OR "
    "raw CSV/spreadsheet text exported from a school's own system with its own column names and order. "
    "The text may be messy or have OCR errors. Extract every staff member row you can confidently identify. For each, "
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
