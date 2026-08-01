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
