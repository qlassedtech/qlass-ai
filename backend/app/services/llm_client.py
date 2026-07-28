import anthropic
from app.config import settings

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None


async def call_llm(system_prompt: str, messages: list[dict], model: str = "claude-sonnet-4-6") -> str:
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
        return (
            "[LLM not configured] Set ANTHROPIC_API_KEY in .env to get real replies. "
            f"Would have answered: {last_message}"
        )

    try:
        response = await _client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
        return "".join(block.text for block in response.content if block.type == "text")
    except anthropic.APIError as exc:
        return (
            "Sorry, I'm having trouble reaching the AI service right now. "
            "Please try again in a bit. "
            f"[LLM error: {exc}]"
        )


async def classify(system_prompt: str, messages: list[dict], fallback: str, model: str = "claude-sonnet-4-6") -> str:
    """
    A separate, deterministic (temperature=0) call for narrow classification
    tasks (e.g. "what language should the reply be in") — kept apart from
    the main creative reply generation so the tutor's actual phrasing keeps
    its normal variation/warmth, while decisions that need to be consistent
    turn-to-turn don't ride on the same non-deterministic sampling.
    """
    if _client is None:
        return fallback

    try:
        response = await _client.messages.create(
            model=model,
            max_tokens=10,
            temperature=0,
            system=system_prompt,
            messages=messages,
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        return text or fallback
    except anthropic.APIError:
        return fallback
