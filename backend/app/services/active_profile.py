import re

import redis.asyncio as redis

from app.config import settings
from app.services.llm_client import classify, LLMResult

# How long a resolved "who's chatting" choice sticks before we ask again —
# long enough to not re-ask mid-conversation, short enough that a different
# family member picking up the same phone hours later gets asked fresh
# rather than silently inheriting a sibling's profile.
ACTIVE_PROFILE_TTL_SECONDS = 6 * 3600

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True) if settings.redis_url else None

_ROUTING_MAX_TOKENS = 60

_SYSTEM_PROMPT_TEMPLATE = (
    "This WhatsApp number belongs to a family with more than one student profile "
    "registered on Qlass: {names}. Read the student's latest message and respond "
    "with ONLY one line in EXACTLY this format, nothing else:\n"
    '[[ROUTE new_profile=<yes|no> switch_target="<name>|NONE" name_match="<name>|NONE"]]\n\n'
    "new_profile — yes ONLY if the message is clearly asking to set up an ADDITIONAL "
    'new sibling/student profile (e.g. "this is for my other son", "add my younger '
    'daughter", "naya student", "different child") — NOT a request to switch to an '
    "EXISTING profile below, and not an ordinary question.\n"
    'switch_target — if the message explicitly asks to switch to a named profile '
    '(e.g. "switch to Priya", "it\'s Raj now", "this is Priya talking", "switch to '
    'Timmy" even if Timmy isn\'t in the list above), the name as stated by the '
    "student; otherwise NONE. Output whatever name they said even if it doesn't "
    "match anyone in the list — the caller checks that separately.\n"
    "name_match — if the message plainly mentions one of the EXISTING names above "
    "anywhere (e.g. the student signs off with their name, or a disambiguation reply "
    "just states their name), that name; otherwise NONE. A name mention that's purely "
    "incidental to unrelated content still counts (e.g. mentioning a historical "
    "person who happens to share the name does NOT count — only count it when it "
    "plausibly identifies who is texting)."
)


class ProfileRouting:
    def __init__(self, new_profile_request: bool, switch_target: str | None, name_match: str | None, llm_result: LLMResult):
        self.new_profile_request = new_profile_request
        self.switch_target = switch_target
        self.name_match = name_match
        self.llm_result = llm_result


def _field(block: str, name: str) -> str | None:
    match = re.search(rf'\b{re.escape(name)}=("[^"]*"|[A-Za-z_]+)', block)
    if not match:
        return None
    value = match.group(1)
    return value[1:-1] if value.startswith('"') else value


def _resolve_name(raw: str | None, known_names: list[str]) -> str | None:
    """Only ever returns a name actually in known_names — the model is asked
    not to invent one, but this is the hard guarantee against a hallucinated
    or mis-cased name silently switching the active profile to nobody."""
    if not raw or raw.upper() == "NONE":
        return None
    for name in known_names:
        if name.lower() == raw.strip().lower():
            return name
    return None


async def classify_profile_routing(message_text: str, known_names: list[str]) -> ProfileRouting:
    """
    Replaces the old regex phrase-lists (looks_like_new_profile_request,
    extract_switch_target_name, match_student_by_name) with one AI call —
    those only ever matched a fixed set of exact phrasings/patterns (e.g.
    "switch to Raj" but not "can Raj take over now"), and match_student_by_name's
    substring-then-word-boundary matching was still a blunt tool for genuinely
    ambiguous phrasing. known_names constrains the model to the real profiles
    on this phone so it can never invent or switch to a name that doesn't exist.
    """
    if not known_names:
        return ProfileRouting(False, None, None, LLMResult(text="", model="none"))

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(names=", ".join(known_names))
    result = await classify(
        system_prompt,
        [{"role": "user", "content": message_text}],
        fallback='[[ROUTE new_profile=no switch_target=NONE name_match=NONE]]',
        model="claude-haiku-4-5-20251001",
        max_tokens=_ROUTING_MAX_TOKENS,
    )
    match = re.search(r"\[\[ROUTE\s+(.*?)\]\]", result.text, re.DOTALL)
    block = match.group(1) if match else result.text

    new_profile_request = _field(block, "new_profile") == "yes"
    switch_target_raw = _field(block, "switch_target")
    switch_target = switch_target_raw.strip() if switch_target_raw and switch_target_raw.upper() != "NONE" else None
    # name_match is only ever used to silently resolve an ambiguous phone to
    # one of its EXISTING profiles, never to switch to an unknown name — so
    # it's hard-constrained to the known list, unlike switch_target above
    # (which deliberately surfaces an unknown name so the caller can offer
    # to create a new profile for it rather than silently doing nothing).
    name_match = _resolve_name(_field(block, "name_match"), known_names)
    return ProfileRouting(new_profile_request, switch_target, name_match, result)


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
