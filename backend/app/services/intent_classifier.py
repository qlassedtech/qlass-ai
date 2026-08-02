from app.services.llm_client import classify, LLMResult
from app.services.retrieval import RetrievedChunk

# The narrow, closed set of free/no-tutoring-LLM commands this product
# special-cases outside of an ordinary tutoring question — kept as a fixed
# set (not open-ended labels) so a classifier miss degrades safely to
# "other" (treated as a normal tutoring message) rather than an
# unrecognized label breaking downstream routing.
INTENTS = ("menu", "progress", "referral", "teacher_help", "quiz_stop", "other")

_CLASSIFY_MAX_TOKENS = 100
_EXCERPT_PREVIEW_CHARS = 300

_SYSTEM_PROMPT = (
    "Classify the student's latest WhatsApp message. Respond with ONLY one line in "
    'EXACTLY this format, nothing else — no explanation, no markdown:\n'
    '[[CLASSIFY intent=<label> wants_quiz=<yes|no> quiz_topic="<topic>|NONE" '
    'wants_mock_test=<yes|no> mock_test_topic="<topic>|NONE" quiz_skip=<yes|no> '
    'relevant_excerpts=<comma-separated numbers|NONE>]]\n\n'
    "intent — exactly one of:\n"
    "menu — asking to see the main menu of options, or a bare \"help\"/\"options\" "
    "with no actual question (e.g. \"menu\", \"help\", \"what can you do\"). CRITICAL — "
    "if a \"Last assistant message\" is given below and it ends with a question, a short "
    "reply is almost always answering THAT question, not asking for a menu — even if the "
    "word itself sounds vaguely menu-like (e.g. the tutor asked \"want to explore a little "
    "beyond that?\" and the student replied \"Explore\": that's agreeing to explore, not "
    "asking to see options). Only classify menu when there's no pending question to answer, "
    "or the message unmistakably asks for help/commands regardless of context.\n"
    "progress — asking how they're doing, their score, or a progress/performance "
    "report (e.g. \"how am I doing\", \"my progress\", \"what is my performance\", "
    "\"kaisa kar raha hoon\", \"mera score kya hai\").\n"
    "referral — asking about referring/inviting a friend for credits (e.g. "
    "\"refer a friend\", \"referral code\", \"invite karna hai\").\n"
    "teacher_help — explicitly asking to talk to, contact, or get help from "
    "their (human) teacher (e.g. \"talk to my teacher\", \"contact teacher\", "
    "\"teacher se baat karni hai\") — NOT a request to explain something using "
    "an analogy involving a teacher, and not a normal tutoring question.\n"
    "quiz_stop — asking to stop, end, quit, or exit the WHOLE quiz/mock test currently "
    "in progress (e.g. \"stop quiz\", \"end this\", \"quiz band karo\") — the student wants "
    "out of the quiz entirely, not just past this one question. NOT the same as not knowing "
    "the answer to the current question: \"I don't know\", \"we didn't study this\", \"we "
    "haven't learned this yet\", or similar are quiz_skip (see below), not quiz_stop — the "
    "quiz should keep going through its remaining questions in that case, only ending early "
    "when the student clearly wants to quit the whole thing.\n"
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
    "\"i don't know\", \"idk\", \"no idea\", \"we didn't study this\", \"we haven't learned "
    "this yet\") rather than actually attempting an answer. This is a pass on THIS ONE "
    "question only — the quiz keeps going afterward, it does not end the quiz (that's "
    "quiz_stop above, a different and much rarer intent). This is irrelevant (and safely "
    "ignored) when no quiz is active — still fill it in based on the message alone.\n\n"
    "relevant_excerpts — if a \"Candidate excerpts\" list is given below, decide which "
    "numbered excerpts (if any) are genuinely useful for answering or responding to this "
    "specific message — sharing a single common word with the message is not enough, an "
    "excerpt only counts if it's actually about the same topic. Most everyday replies "
    "(greetings, \"I don't know\", short answers, thanks) have NO relevant excerpt at all, "
    "and that's the correct, expected answer for most messages — don't force a match. "
    "If no \"Candidate excerpts\" list is given, or none qualify, this is NONE."
)


def _field(block: str, name: str) -> str | None:
    """
    Plain string scan for `name=value` inside the fixed-format CLASSIFY
    block — value is either a "quoted string" (kept verbatim, spaces and
    all) or a bare run of non-whitespace characters.
    """
    marker = f"{name}="
    idx = block.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    if start < len(block) and block[start] == '"':
        end = block.find('"', start + 1)
        return block[start + 1:end] if end != -1 else None
    end = start
    while end < len(block) and not block[end].isspace():
        end += 1
    return block[start:end] or None


def _extract_tag_block(raw_text: str) -> str:
    marker = "[[CLASSIFY"
    start = raw_text.find(marker)
    if start == -1:
        return raw_text
    start += len(marker)
    end = raw_text.find("]]", start)
    return raw_text[start:end] if end != -1 else raw_text[start:]


def _parse_relevant_excerpts(value: str | None) -> list[int]:
    if not value:
        return []
    indices = []
    for piece in value.split(","):
        piece = piece.strip()
        if piece.isdigit():
            indices.append(int(piece))
    return indices


class MessageClassification:
    def __init__(
        self, intent: str, quiz_topic: str | None, mock_test_topic: str | None,
        wants_mock_test: bool, quiz_skip: bool, llm_result: LLMResult,
        relevant_excerpts: list[int] | None = None,
    ):
        self.intent = intent
        self.quiz_topic = quiz_topic
        self.mock_test_topic = mock_test_topic
        self.wants_mock_test = wants_mock_test
        self.quiz_skip = quiz_skip
        self.llm_result = llm_result
        self.relevant_excerpts = relevant_excerpts or []


def _parse_classification(raw_text: str) -> dict:
    block = _extract_tag_block(raw_text)

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
    relevant_excerpts = _parse_relevant_excerpts(_field(block, "relevant_excerpts"))

    return {
        "intent": intent,
        "quiz_topic": quiz_topic,
        "wants_mock_test": wants_mock_test,
        "mock_test_topic": mock_test_topic,
        "quiz_skip": quiz_skip,
        "relevant_excerpts": relevant_excerpts,
    }


async def classify_intent(
    message_text: str, last_discussed_topic: str | None = None, last_assistant_message: str | None = None,
    candidate_chunks: list[RetrievedChunk] | None = None,
) -> MessageClassification:
    """
    A single deterministic (temperature=0) classification call replacing
    both the old plain intent-label classifier AND the separate regex-based
    quiz/mock-test/skip detection that used to live in quiz_service.py —
    folded into one call rather than several, since this already runs on
    every message regardless (see app.routers.whatsapp). The RAG relevance
    judgment (relevant_excerpts) rides along in this same call too, for the
    same reason — see app.services.retrieval's module docstring for why
    that isn't its own dedicated LLM call.

    The regex phrase-lists this replaces only ever matched an exact fixed
    string (e.g. "quiz me on X"), so any other phrasing silently fell
    through — and they had no way to resolve a vague reference like "quiz
    on the same" without extra fallback logic in the caller. Passing
    last_discussed_topic as context lets the model resolve that directly,
    in the same call, rather than a separate is_vague_quiz_topic check.

    last_assistant_message is the tutor's own immediately preceding reply —
    without it, a short answer to a question the tutor itself just asked
    (e.g. tutor: "want to explore a little beyond that?", student:
    "Explore") reads as ambiguous on its own and can get misclassified as
    "menu" purely on surface wording, even though it's really just an
    answer (confirmed live).
    """
    context_lines = []
    if last_discussed_topic:
        context_lines.append(f'[Last discussed topic: "{last_discussed_topic}"]')
    if last_assistant_message:
        context_lines.append(f'[Last assistant message: "{last_assistant_message}"]')
    if candidate_chunks:
        excerpt_lines = [
            f"{i}. [{chunk.chapter or 'unknown chapter'}] {chunk.content[:_EXCERPT_PREVIEW_CHARS]}"
            for i, chunk in enumerate(candidate_chunks, start=1)
        ]
        context_lines.append("[Candidate excerpts:\n" + "\n\n".join(excerpt_lines) + "]")
    context = "\n".join([*context_lines, message_text]) if context_lines else message_text

    result = await classify(
        _SYSTEM_PROMPT,
        [{"role": "user", "content": context}],
        fallback="[[CLASSIFY intent=other wants_quiz=no quiz_topic=NONE wants_mock_test=no "
                  "mock_test_topic=NONE quiz_skip=no relevant_excerpts=NONE]]",
        model="claude-haiku-4-5-20251001",
        max_tokens=_CLASSIFY_MAX_TOKENS,
    )
    parsed = _parse_classification(result.text)
    return MessageClassification(
        intent=parsed["intent"], quiz_topic=parsed["quiz_topic"], mock_test_topic=parsed["mock_test_topic"],
        wants_mock_test=parsed["wants_mock_test"], quiz_skip=parsed["quiz_skip"], llm_result=result,
        relevant_excerpts=parsed["relevant_excerpts"],
    )
