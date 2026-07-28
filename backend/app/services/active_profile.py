import redis.asyncio as redis

from app.config import settings

# How long a resolved "who's chatting" choice sticks before we ask again —
# long enough to not re-ask mid-conversation, short enough that a different
# family member picking up the same phone hours later gets asked fresh
# rather than silently inheriting a sibling's profile.
ACTIVE_PROFILE_TTL_SECONDS = 6 * 3600

_NEW_PROFILE_PHRASES = [
    "different child", "different student", "different kid", "another child", "another student",
    "add my other", "add another", "naya student", "dusra bachcha", "dusri bacchi",
    "switch profile", "switch student", "switch child", "i am a different", "this is not",
]

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True) if settings.redis_url else None


def looks_like_new_profile_request(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _NEW_PROFILE_PHRASES)


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
    enough signal to skip an explicit confirmation step."""
    lowered = text.lower().strip()
    for student in students:
        if student.name and student.name.lower() != "new student" and student.name.lower() in lowered:
            return student
    return None
