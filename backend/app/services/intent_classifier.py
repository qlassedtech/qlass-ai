import re

from app.services.llm_client import classify, LLMResult

# The narrow, closed set of free/no-tutoring-LLM commands this product
# special-cases outside of an ordinary tutoring question — kept as a fixed
# set (not open-ended labels) so a classifier miss degrades safely to
# "other" (treated as a normal tutoring message) rather than an
# unrecognized label breaking downstream routing.
INTENTS = ("menu", "progress", "referral", "teacher_help", "quiz_stop", "other")

_CLASSIFY_MAX_TOKENS = 80

_SYSTEM_PROMPT = (
    "Classify the student's latest WhatsApp message. Respond with ONLY one line in "
    'EXACTLY this format, nothing else — no explanation, no markdown:\n'
    '[[CLASSIFY intent=<label> wants_quiz=<yes|no> quiz_topic="<topic>|NONE" '
    'wants_mock_test=<yes|no> mock_test_topic="<topic>|NONE" quiz_skip=<yes|no>]]\n\n'
    "intent — exactly one of:\n"
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
    "quiz_stop — asking to stop, end, quit, or exit a QUIZ/mock test currently "
    "in progress (e.g. \"stop quiz\", \"end this\", \"quiz band karo\").\n"
    "other — anything else at all: a real academic question, an answer to a quiz "
    "question, small talk, profile info, etc. This is the default — when in doubt, "
    "choose other.\n\n"
    "wants_quiz / quiz_topic — true if the student is asking to be QUIZZED (a short "
    "ad-hoc test) on something right now (e.g. \"quiz me on circular motion\", \"test "
    "me on this\", \"give me a quiz\"). quiz_topic is the actual topic to quiz on: if "
    "the student named a specific topic, use it; if they used a vague reference "
    "(\"quiz on the same\", \"quiz me on this\") or gave no topic at all (\"quiz me "
    "now\", \"give me the quiz\"), resolve it yourself using the \"Last discussed "
    "topic\" context provided in the message below — output that resolved topic "
    "name directly, never the literal vague words. If wants_quiz is no, or no topic "
    "can be resolved at all, quiz_topic is NONE.\n\n"
    "wants_mock_test / mock_test_topic — true if the student is asking for a longer, "
    "timed, board-exam-style practice test (e.g. \"mock test\", \"give me a full mock "
    "exam\", \"board exam practice on optics\"), as opposed to the short ad-hoc quiz "
    "above. mock_test_topic is the specific topic if one was named, or NONE for a "
    "general mixed-topic mock test (a bare \"mock test\" with no topic is still "
    "wants_mock_test=yes, mock_test_topic=NONE — that's a valid, common request, not "
    "a reason to say no).\n\n"
    "quiz_skip — true ONLY if the message is clearly trying to skip/pass on answering "
    "a currently-active quiz question (e.g. \"skip\", \"pass\", \"next question\", "
    "\"i don't know\", \"idk\", \"no idea\") rather than actually attempting an answer. "
    "This is irrelevant (and safely ignored) when no quiz is active — still fill it in "
    "based on the message alone."
)


def _field(block: str, name: str) -> str | None:
    match = re.search(rf'\b{re.escape(name)}=("[^"]*"|[A-Za-z_]+)', block)
    if not match:
        return None
    value = match.group(1)
    return value[1:-1] if value.startswith('"') else value


class MessageClassification:
    def __init__(
        self, intent: str, quiz_topic: str | None, mock_test_topic: str | None,
        wants_mock_test: bool, quiz_skip: bool, llm_result: LLMResult,
    ):
        self.intent = intent
        self.quiz_topic = quiz_topic
        self.mock_test_topic = mock_test_topic
        self.wants_mock_test = wants_mock_test
        self.quiz_skip = quiz_skip
        self.llm_result = llm_result


def _parse_classification(raw_text: str) -> dict:
    match = re.search(r"\[\[CLASSIFY\s+(.*?)\]\]", raw_text, re.DOTALL)
    block = match.group(1) if match else raw_text

    intent_raw = (_field(block, "intent") or "other").lower()
    intent = intent_raw if intent_raw in INTENTS else "other"

    wants_quiz = _field(block, "wants_quiz") == "yes"
    quiz_topic_raw = _field(block, "quiz_topic")
    quiz_topic = quiz_topic_raw.strip() if wants_quiz and quiz_topic_raw and quiz_topic_raw.upper() != "NONE" else None

    wants_mock_test = _field(block, "wants_mock_test") == "yes"
    mock_topic_raw = _field(block, "mock_test_topic")
    mock_test_topic = (
        mock_topic_raw.strip() if wants_mock_test and mock_topic_raw and mock_topic_raw.upper() != "NONE" else None
    )

    quiz_skip = _field(block, "quiz_skip") == "yes"

    return {
        "intent": intent,
        "quiz_topic": quiz_topic,
        "wants_mock_test": wants_mock_test,
        "mock_test_topic": mock_test_topic,
        "quiz_skip": quiz_skip,
    }


async def classify_intent(message_text: str, last_discussed_topic: str | None = None) -> MessageClassification:
    """
    A single deterministic (temperature=0) classification call replacing
    both the old plain intent-label classifier AND the separate regex-based
    quiz/mock-test/skip detection that used to live in quiz_service.py —
    folded into one call rather than several, since this already runs on
    every message regardless (see app.routers.whatsapp).

    The regex phrase-lists this replaces only ever matched an exact fixed
    string (e.g. "quiz me on X"), so any other phrasing silently fell
    through — and they had no way to resolve a vague reference like "quiz
    on the same" without extra fallback logic in the caller. Passing
    last_discussed_topic as context lets the model resolve that directly,
    in the same call, rather than a separate is_vague_quiz_topic check.
    """
    context = message_text
    if last_discussed_topic:
        context = f'[Last discussed topic: "{last_discussed_topic}"]\n{message_text}'

    result = await classify(
        _SYSTEM_PROMPT,
        [{"role": "user", "content": context}],
        fallback="[[CLASSIFY intent=other wants_quiz=no quiz_topic=NONE wants_mock_test=no mock_test_topic=NONE quiz_skip=no]]",
        model="claude-haiku-4-5-20251001",
        max_tokens=_CLASSIFY_MAX_TOKENS,
    )
    parsed = _parse_classification(result.text)
    return MessageClassification(
        intent=parsed["intent"], quiz_topic=parsed["quiz_topic"], mock_test_topic=parsed["mock_test_topic"],
        wants_mock_test=parsed["wants_mock_test"], quiz_skip=parsed["quiz_skip"], llm_result=result,
    )
