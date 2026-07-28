import re

from app.models.core import Student

# Ask at most one profile question every N user messages, and only if a
# field is still missing — keeps it natural instead of interrogating the
# student on every reply.
ASK_EVERY_N_MESSAGES = 3

# Ordered by how much each field improves answer quality; asked one at a time.
# "name" comes first — collected here rather than at signup since there's
# no signup flow; also the only way to tell two students apart on a shared
# family phone (see app.services.active_profile), so it matters beyond
# just personalization.
PROFILE_QUESTIONS: list[tuple[str, str]] = [
    ("name", "By the way, what's your name? 😊"),
    ("class_", "Also, which class are you in? That'll help me tailor my answers better. 📚"),
    ("board", "Which board are you studying under — CBSE, ICSE, or a State board?"),
    ("school", "One more thing — which school do you study at?"),
]

_PLACEHOLDER_NAME = "New Student"


def next_missing_field(student: Student) -> tuple[str, str] | None:
    for field, question in PROFILE_QUESTIONS:
        value = getattr(student, field)
        if field == "name":
            if not value or value == _PLACEHOLDER_NAME:
                return field, question
            continue
        if not value:
            return field, question
    return None


def should_ask_this_turn(user_message_count: int) -> bool:
    return user_message_count % ASK_EVERY_N_MESSAGES == 0


_QUESTION_STARTERS = re.compile(
    r"^\s*(what|why|how|when|who|which|where|can|could|is|are|do|does)\b", re.IGNORECASE
)


def looks_like_answer(field: str, raw_text: str) -> bool:
    """
    Best-effort check that the student is actually answering the pending
    profile question rather than asking something unrelated — so we don't
    store a stray question as e.g. their board name.
    """
    raw_text = raw_text.strip()
    if not raw_text or "?" in raw_text or _QUESTION_STARTERS.match(raw_text):
        return False
    if field == "class_":
        return bool(re.search(r"\d{1,2}", raw_text))
    return len(raw_text) <= 40  # a board/school name, not a full sentence


def looks_like_confirmation_reply(raw_text: str) -> bool:
    """
    Best-effort check that the student is actually answering a yes/no
    confirmation question rather than ignoring it and asking something
    else — same idea as looks_like_answer, for the class-update suggestion.
    """
    raw_text = raw_text.strip()
    return bool(raw_text) and "?" not in raw_text and not _QUESTION_STARTERS.match(raw_text)


def clean_answer(field: str, raw_text: str) -> str:
    raw_text = raw_text.strip()
    if field == "class_":
        match = re.search(r"\d{1,2}", raw_text)
        if match:
            return match.group(0)
    return raw_text
