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

# A student's raw message can legitimately mix a non-answer with the real
# one in the same breath (e.g. "I don't know. Nikhil" — declining a check
# question AND stating their name) — the LLM extracting profile_answer is
# told to pull out just the clean value, but confirmed live it sometimes
# doesn't: a real student's name ended up permanently stored as the whole
# raw phrase "I don't know. Nikhil" instead of "Nikhil". Since whatever
# lands here is trusted verbatim with no other check, and a field is never
# re-asked once non-blank (see next_missing_field), a bad extraction was
# permanent. These are a cheap backstop, not a replacement for the LLM
# doing it right — reject anything implausible rather than silently
# accepting it, so the field stays blank and gets asked again later
# instead of being permanently wrong.
_NON_ANSWER_PATTERNS = (
    "don't know", "dont know", "don't no", "not sure", "no idea", "nahi pata", "pata nahi", "idk",
)
_MAX_ANSWER_CHARS = {"name": 50, "class_": 10, "board": 40, "school": 100}


def is_plausible_profile_answer(field: str, value: str) -> bool:
    value_lower = value.lower()
    if any(pattern in value_lower for pattern in _NON_ANSWER_PATTERNS):
        return False
    return len(value) <= _MAX_ANSWER_CHARS.get(field, 60)


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
