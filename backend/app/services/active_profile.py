import re

import redis.asyncio as redis

from app.config import settings

# How long a resolved "who's chatting" choice sticks before we ask again —
# long enough to not re-ask mid-conversation, short enough that a different
# family member picking up the same phone hours later gets asked fresh
# rather than silently inheriting a sibling's profile.
ACTIVE_PROFILE_TTL_SECONDS = 6 * 3600

# Phrases that mean "set up a NEW sibling profile" — distinct from
# switching to an EXISTING one (see extract_switch_target_name below).
# Deliberately excludes anything like "switch to <name>", since that should
# try to match an existing profile first, not blindly create a new one.
_NEW_PROFILE_PHRASES = [
    "different child", "different student", "different kid", "another child", "another student",
    "add my other", "add another", "naya student", "dusra bachcha", "dusri bacchi",
]

# "switch to Priya", "it's Raj now", "this is Priya", "switch profile to Raj"
_SWITCH_PATTERNS = [
    r"switch (?:profile )?to (\w+)", r"it'?s (\w+) now", r"this is (\w+)(?: talking| here)?$",
    r"switch (?:to )?(\w+)'s profile",
]

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True) if settings.redis_url else None


def looks_like_new_profile_request(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _NEW_PROFILE_PHRASES)


def extract_switch_target_name(text: str) -> str | None:
    lowered = text.lower().strip()
    for pattern in _SWITCH_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            return match.group(1)
    return None


async def get_active_profile(phone: str) -> int | None:
    if _redis is None:
        return None
    value = await _redis.get(f"activeprofile:{phone}")
    return int(value) if value else None


async def set_active_profile(phone: str, student_id: int) -> None:
    if _redis is None:
        return
    await _redis.set(f"activeprofile:{phone}", str(student_id), ex=ACTIVE_PROFILE_TTL_SECONDS)


async def clear_active_profile(phone: str) -> None:
    if _redis is None:
        return
    await _redis.delete(f"activeprofile:{phone}")


def build_disambiguation_prompt(student_names: list[str]) -> str:
    names_str = " or ".join([", ".join(student_names[:-1]), student_names[-1]]) if len(student_names) > 1 else student_names[0]
    return f"Quick check — who's messaging right now, {names_str}? Just reply with the name. 🙂"


def match_student_by_name(students: list, text: str):
    """Best-effort match of a disambiguation reply (or any message) against
    known profile names on this phone — a real name mention is a strong
    enough signal to skip an explicit confirmation step.

    Matched on a whole word, not a bare substring — a plain `name in text`
    check would false-positive on short/common Indian names that happen to
    appear inside unrelated words (e.g. a student named "Om" would "match"
    any message containing "tomorrow", "welcome", or "phenomenon"; "Ria"
    would match "Maria"), silently switching the active profile to the
    wrong sibling on an ordinary tutoring question.
    """
    lowered = text.lower().strip()
    for student in students:
        if not student.name or student.name.lower() == "new student":
            continue
        if re.search(rf"\b{re.escape(student.name.lower())}\b", lowered):
            return student
    return None
