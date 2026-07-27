import anthropic
from app.config import settings

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None


def call_llm(system_prompt: str, user_message: str, model: str = "claude-sonnet-4-6") -> str:
    """
    Single point of contact with the LLM. Swap `model` or the provider
    here later without touching any agent code.
    """
    if _client is None:
        return (
            "[LLM not configured] Set ANTHROPIC_API_KEY in .env to get real replies. "
            f"Would have answered: {user_message}"
        )

    response = _client.messages.create(
        model=model,
        max_tokens=800,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return "".join(block.text for block in response.content if block.type == "text")
