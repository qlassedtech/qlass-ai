from app.services.llm_client import classify, LLMResult

# The narrow, closed set of free/no-tutoring-LLM commands this product
# special-cases outside of an ordinary tutoring question — kept as a fixed
# set (not open-ended labels) so a classifier miss degrades safely to
# "other" (treated as a normal tutoring message) rather than an
# unrecognized label breaking downstream routing.
INTENTS = ("menu", "progress", "referral", "teacher_help", "quiz_stop", "other")

_INTENT_CLASSIFIER_PROMPT = (
    "Classify the student's latest WhatsApp message into EXACTLY ONE of these "
    "labels. Respond with ONLY the label itself, nothing else — no punctuation, "
    "no explanation.\n\n"
    "menu — asking to see the main menu of options, or a bare \"help\"/\"options\" "
    "with no actual question (e.g. \"menu\", \"help\", \"what can you do\").\n"
    "progress — asking how they're doing, their score, or a progress/performance "
    "report (e.g. \"how am I doing\", \"my progress\", \"what is my performance\", "
    "\"kaisa kar raha hoon\", \"mera score kya hai\").\n"
    "referral — asking about referring/inviting a friend for credits (e.g. "
    "\"refer a friend\", \"referral code\", \"invite karna hai\").\n"
    "teacher_help — explicitly asking to talk to, contact, or get help from "
    "their (human) teacher (e.g. \"talk to my teacher\", \"contact teacher\", "
    "\"teacher se baat karni hai\") — NOT a request to explain something using "
    "an analogy involving a teacher, and not a normal tutoring question.\n"
    "quiz_stop — asking to stop, end, quit, or exit a quiz/mock test currently "
    "in progress (e.g. \"stop quiz\", \"end this\", \"quiz band karo\").\n"
    "other — anything else at all: a real academic question, a quiz/mock test "
    "request, an answer to a quiz question, small talk, profile info, etc. "
    "This is the default — when in doubt, choose other.\n"
)


async def classify_intent(message_text: str) -> LLMResult:
    """
    A separate, deterministic (temperature=0), cheap classification call —
    same pattern as TutorAgent's language classifier — replacing what used
    to be several independent hardcoded phrase-lists (one per intent) that
    only matched exact English/Hinglish strings and silently missed any
    other phrasing (e.g. "what is my performance" never matched the fixed
    "my progress" list, so it fell through to the LLM improvising an answer
    instead of the real, data-backed progress report).

    Returns the raw LLMResult so the caller can both read `.text` (via
    parse_intent) and record its token usage/cost, same as any other LLM
    call in this codebase.
    """
    return await classify(
        _INTENT_CLASSIFIER_PROMPT,
        [{"role": "user", "content": message_text}],
        fallback="other",
        model="claude-haiku-4-5-20251001",
    )


def parse_intent(raw_text: str) -> str:
    intent = raw_text.strip().lower()
    return intent if intent in INTENTS else "other"
