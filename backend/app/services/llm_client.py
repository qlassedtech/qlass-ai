from dataclasses import dataclass

import anthropic
from app.config import settings

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None


@dataclass
class LLMResult:
    """Reply text plus token usage, so callers can record actual API cost."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


async def call_llm(system_prompt: str, messages: list[dict], model: str = "claude-sonnet-4-6") -> LLMResult:
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
            system=system_prompt,
            messages=messages,
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResult(
            text=text, model=model, input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens
        )
    except anthropic.APIError as exc:
        return LLMResult(
            text=(
                "Sorry, I'm having trouble reaching the AI service right now. "
                "Please try again in a bit. "
                f"[LLM error: {exc}]"
            ),
            model=model,
        )


async def classify(system_prompt: str, messages: list[dict], fallback: str, model: str = "claude-sonnet-4-6") -> LLMResult:
    """
    A separate, deterministic (temperature=0) call for narrow classification
    tasks (e.g. "what language should the reply be in") — kept apart from
    the main creative reply generation so the tutor's actual phrasing keeps
    its normal variation/warmth, while decisions that need to be consistent
    turn-to-turn don't ride on the same non-deterministic sampling.
    """
    if _client is None:
        return LLMResult(text=fallback, model=model)

    try:
        response = await _client.messages.create(
            model=model,
            max_tokens=10,
            temperature=0,
            system=system_prompt,
            messages=messages,
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        return LLMResult(
            text=text or fallback,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
    except anthropic.APIError:
        return LLMResult(text=fallback, model=model)


async def translate_with_claude(
    text: str, target_language_code: str, model: str = "claude-haiku-4-5-20251001"
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
    """
    if _client is None:
        return None

    lang_name = "Hindi" if target_language_code.startswith("hi") else target_language_code
    system_prompt = (
        f"Translate the given text into natural, colloquial {lang_name} as spoken in Bihar, India. "
        "Keep it warm and conversational, the way a friendly tutor talks to a student — not stiff or "
        "overly literal. Preserve all markdown formatting (*bold* markers) and line breaks exactly as "
        "in the original. Output ONLY the translated text — no preamble, no quotes, no explanation."
    )
    try:
        response = await _client.messages.create(
            model=model,
            max_tokens=1024,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": text}],
        )
        translated = "".join(block.text for block in response.content if block.type == "text").strip()
        if not translated:
            return None
        return LLMResult(
            text=translated, model=model, input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens
        )
    except anthropic.APIError:
        return None
