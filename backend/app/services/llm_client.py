import logging
from dataclasses import dataclass

import anthropic
from app.config import settings

logger = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None


@dataclass
class LLMResult:
    """Reply text plus token usage, so callers can record actual API cost."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


def _cached_system(system_prompt: str | list[dict]) -> list[dict]:
    """
    Marks the system prompt as cacheable. A plain string (every caller
    except app.agents.tutor_agent) becomes one cache-marked block — the
    whole thing is expected to be identical call-to-call within a session.

    A caller can instead pass its own pre-built list of blocks — this is
    what tutor_agent.TutorAgent.respond does, splitting its system prompt
    into a large STATIC block (cache-marked) and a small DYNAMIC tail
    (not cached) that varies almost every turn (RAG chunks, a pinned
    document). Bundling those together as one cache-marked block, as this
    function used to do unconditionally, meant the cache prefix changed on
    nearly every real call — confirmed live, the ~3,000-token static
    instructional prompt was being billed as a fresh read every single
    time instead of the ~90%-cheaper cache read it was designed for,
    because SOMETHING later in that same string almost always differed.
    Passed through unchanged here since the caller already set its own
    cache_control placement.
    """
    if isinstance(system_prompt, list):
        return system_prompt
    return [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]


async def call_llm(system_prompt: str | list[dict], messages: list[dict], model: str = "claude-sonnet-4-6") -> LLMResult:
    """
    Single point of contact with the LLM. Swap `model` or the provider
    here later without touching any agent code.

    `messages` is the full conversation so far, oldest first:
    [{"role": "user"|"assistant", "content": "..."}, ...] ending with the
    latest user message.

    Uses AsyncAnthropic so a slow LLM call doesn't block FastAPI's event loop
    from handling other incoming webhook requests concurrently (a sync client
    call here would stall every other request for the full LLM round-trip).
    """
    if _client is None:
        last_message = messages[-1]["content"] if messages else ""
        return LLMResult(
            text=(
                "[LLM not configured] Set ANTHROPIC_API_KEY in .env to get real replies. "
                f"Would have answered: {last_message}"
            ),
            model=model,
        )

    try:
        response = await _client.messages.create(
            model=model,
            max_tokens=1024,
            system=_cached_system(system_prompt),
            messages=messages,
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResult(
            text=text,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_write_tokens=response.usage.cache_creation_input_tokens or 0,
            cache_read_tokens=response.usage.cache_read_input_tokens or 0,
        )
    except anthropic.APIError as exc:
        # The raw exception (often the provider's full JSON error body) goes
        # to server logs only — a student should never see internal API
        # error payloads in their chat, confirmed live: a 400/500 from
        # Anthropic was showing up verbatim as the tutor's "reply".
        logger.error("Anthropic API error in call_llm (model=%s): %s", model, exc)
        return LLMResult(
            text="Sorry, I'm having trouble reaching the AI service right now. Please try again in a bit.",
            model=model,
        )


async def classify(
    system_prompt: str, messages: list[dict], fallback: str, model: str = "claude-sonnet-4-6", max_tokens: int = 10,
) -> LLMResult:
    """
    A separate, deterministic (temperature=0) call for narrow classification
    tasks (e.g. "what language should the reply be in") — kept apart from
    the main creative reply generation so the tutor's actual phrasing keeps
    its normal variation/warmth, while decisions that need to be consistent
    turn-to-turn don't ride on the same non-deterministic sampling.

    max_tokens defaults to 10 (enough for a single bare label like "hi-IN"
    or "progress") — callers whose classifier returns a structured
    multi-field tag (e.g. a resolved quiz topic string) must pass a larger
    value or the response gets truncated mid-tag.
    """
    if _client is None:
        return LLMResult(text=fallback, model=model)

    try:
        response = await _client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,
            system=_cached_system(system_prompt),
            messages=messages,
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        return LLMResult(
            text=text or fallback,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_write_tokens=response.usage.cache_creation_input_tokens or 0,
            cache_read_tokens=response.usage.cache_read_input_tokens or 0,
        )
    except anthropic.APIError as exc:
        logger.error("Anthropic API error in classify (model=%s): %s", model, exc)
        return LLMResult(text=fallback, model=model)


async def translate_with_claude(
    text: str,
    target_language_code: str,
    model: str = "claude-haiku-4-5-20251001",
    speaker_gender: str | None = None,
    student_state: str | None = None,
) -> LLMResult | None:
    """
    Translate the tutor's English reply into the student's language using
    Claude (Haiku, for cost) instead of a dedicated translation API.

    Sarvam's Mayura charges per character and was consistently the single
    biggest cost line item in production — bigger than TTS or STT combined —
    since almost every reply to a Bihar student needs translating. Folding
    translation into the same LLM call path we already pay for replaces that
    per-character billing with ordinary (cheap, Haiku-tier) token cost, and
    as a bonus keeps tone/idiom decisions in one model's "voice" rather than
    handing already-natural phrasing to a second, more literal translator.
    Returns None (letting the caller fall back to Sarvam) on any failure,
    rather than silently sending an untranslated English reply.

    `speaker_gender` ("male" or "female") matters for Hindi specifically —
    Hindi verbs conjugate by the SPEAKER's gender for first-person statements
    (करता हूँ vs करती हूँ), and a translation with no gender in mind defaults
    to masculine forms regardless of which TTS voice actually reads it out —
    confirmed live: a female-voiced reply spoke in grammatically masculine
    Hindi, which sounds wrong to a native speaker. This aligns the text's
    grammar with whichever voice (see OPPOSITE_GENDER_SPEAKER) will speak it.

    `student_state` (from Student.state, e.g. "Bihar") drives the cultural
    register — deliberately left for the model to infer per-region rather
    than a hardcoded rule, since the right register (formality, how a tutor
    addresses a student, local idiom) genuinely varies by region and this
    product isn't Bihar-only forever.
    """
    if _client is None:
        return None

    lang_name = "Hindi" if target_language_code.startswith("hi") else target_language_code
    gender_note = ""
    if speaker_gender and lang_name == "Hindi":
        gender_note = (
            f" The speaker is {speaker_gender} — use grammatically {speaker_gender} first-person verb "
            f"forms throughout (e.g. {'करता हूँ, दूंगा' if speaker_gender == 'male' else 'करती हूँ, दूंगी'})."
        )
    region_note = (
        f" The student is in {student_state}, India — use the register, formality, and local idiom a "
        f"tutor from that region would naturally use with a student there, rather than a generic "
        f"one-size-fits-all translation."
        if student_state
        else ""
    )
    system_prompt = (
        f"Translate the given text into natural, colloquial {lang_name} as spoken in India.{gender_note}"
        f"{region_note} Keep it warm and conversational, the way a friendly tutor talks to a student — "
        "not stiff or overly literal. Preserve all markdown formatting (*bold* markers) and line breaks "
        "exactly as in the original. Output ONLY the translated text — no preamble, no quotes, no "
        "explanation."
    )
    try:
        response = await _client.messages.create(
            model=model,
            max_tokens=1024,
            temperature=0,
            system=_cached_system(system_prompt),
            messages=[{"role": "user", "content": text}],
        )
        translated = "".join(block.text for block in response.content if block.type == "text").strip()
        if not translated:
            return None
        return LLMResult(
            text=translated,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_write_tokens=response.usage.cache_creation_input_tokens or 0,
            cache_read_tokens=response.usage.cache_read_input_tokens or 0,
        )
    except anthropic.APIError as exc:
        logger.error("Anthropic API error in translate_with_claude (model=%s): %s", model, exc)
        return None
