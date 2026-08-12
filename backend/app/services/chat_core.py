"""
The single shared "what should the tutor do with this message" core, used
by every channel a student can chat through — WhatsApp, the student web
app, the Android app, and a teacher's own "My AI Tutor" profile (the last
three all go through app.routers.student_app / app.routers.admin, which
call process_web_message below; WhatsApp goes through app.routers.whatsapp).

This was extracted after real drift was found between app.routers.whatsapp
and app.services.student_chat's own separate, incomplete copy of this same
logic: the habit/referral streak-bonus message fired in the wrong order on
non-WhatsApp channels (fixed once, separately, in each file — a bug that
had to be found and fixed twice), and entire intents (menu/progress/
credit_usage/referral/teacher_help) plus features (automatic teacher
escalation after a hint-streak, off-level-class detection, student profile
onboarding nudges, weekly voice/image/video soft caps) were silently
missing outside WhatsApp — a web/app/Android/teacher student asking "what's
my progress" got a generic LLM guess instead of the real computed answer a
WhatsApp student gets for the same question.

Channel-specific concerns stay in each caller, not here: webhook payload
parsing, voice/image/document download+transcription, WhatsApp's own
message-delivery mechanics (voice/image/text fallback chain, interactive
buttons), and the churn/pilot/credit gates that run BEFORE a message ever
reaches this module (those return a value studetn never even sees this
code, so there's nothing to share).

Actually generating audio bytes / image bytes for HTTP-response delivery
on web/app is intentionally NOT done here (yet) — image_prompt/
send_voice_reply are returned as *decisions* the tutor made (which any
channel can act on), but only app.routers.whatsapp currently turns them
into real audio/image files, since that's a frontend media-delivery
feature of its own, not a business-logic gap. Web/app callers can ignore
those two fields for now and just use reply_text.
"""
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.business_rules import FEATURE_LIMITS, TUTOR_LEVEL_MODELS, DEFAULT_TUTOR_LEVEL, TUTOR_LEVEL_NUDGE_OFFERS
from app.config import settings
from app.models.core import ChatHistory, Student, TopicProgress
from app.services import cost_tracker
from app.services.document_client import apply_active_document_pin
from app.services.escalation import get_escalation_recipients, format_escalation_message
from app.services.habit import evaluate_habit_milestones
from app.services.intent_classifier import classify_intent
from app.services.llm_client import translate_with_claude
from app.services.profile_builder import is_plausible_profile_answer, next_missing_field, should_ask_this_turn
from app.services.progress_report import (
    get_student_stats, get_activity_stats, get_welcome_back_note, get_chapter_coverage, format_progress_message,
)
from app.services.quiz_flow import handle_quiz_answer, start_mock_test, start_quiz, stop_quiz
from app.services.referral import (
    generate_referral_code, evaluate_referral_milestones, is_worth_asking_to_refer,
    REFERRAL_SIGNUP_BONUS, REFERRAL_LIFETIME_CAP,
)
from app.services.retrieval import MAX_CANDIDATES, MAX_CHUNKS, fetch_candidate_chunks, fetch_semantic_candidates
from app.services.sarvam_client import translate_text
from app.services.youtube_client import find_best_video
from app.agents.tutor_agent import TutorAgent

logger = logging.getLogger(__name__)

tutor_agent = TutorAgent()

HISTORY_TURNS = 12  # ~6 back-and-forth exchanges of prior context
WEAK_TOPICS_LIMIT = 5
OFF_LEVEL_SUGGEST_THRESHOLD = 3  # consecutive off-level questions before suggesting a class update
USAGE_WARNING_FRACTIONS = [0.5, 0.9, 1.0]  # weekly voice/image/video soft-cap warnings only

# Each button maps to the same canonical phrase its corresponding typed
# command already matches, so a tap is routed through the exact same
# intent-classification path as if the student had typed it. Channels
# without button UI (web/app) get the equivalent options spelled out in
# plain text instead — see the "menu" branch below.
MENU_BUTTONS_BASE = ["📊 My Progress", "💳 Credit Usage"]
MENU_BUTTON_TO_COMMAND = {
    "📊 My Progress": "my progress",
    "💳 Credit Usage": "credit usage",
    "🎁 Refer a Friend": "refer a friend",
}

# Canonical command phrase -> the intent it must always resolve to,
# bypassing classify_intent's guess entirely for these exact messages.
# Confirmed live: a student tapping the "💳 Credit Usage" button (which
# sends the plain text "credit usage") got classify_intent's own "menu"
# intent back instead of "credit_usage", twice in a row — the model
# appears to get thrown off when the immediately preceding assistant
# message is itself a menu that lists "credit usage" as a suggested reply,
# reading the student's message as echoing that suggestion rather than
# actually invoking it. A menu tap is meant to be 100% deterministic (see
# the comment above MENU_BUTTON_TO_COMMAND) — it shouldn't depend on an
# LLM correctly resolving an ambiguity that shouldn't exist in the first
# place.
CANONICAL_COMMAND_INTENT = {
    "my progress": "progress",
    "credit usage": "credit_usage",
    "refer a friend": "referral",
    "change level": "change_level",
    "switch level": "change_level",
    "level 1": "change_level",
    "level 2": "change_level",
    "level 3": "change_level",
    "level 4": "change_level",
    "stay on current level": "change_level",
}

# Level a plain "level N" command switches to — kept a plain dict rather
# than parsing the digit out of message_text at use-site, so an unexpected
# phrasing never risks int()-ing garbage.
LEVEL_COMMAND_TARGET = {"level 1": 1, "level 2": 2, "level 3": 3, "level 4": 4}

TUTOR_LEVEL_LABELS = {
    1: "Level 1 — fastest replies, lightest on credits",
    2: "Level 2 — quick and well-formatted",
    3: "Level 3 — more detailed explanations",
    4: "Level 4 — most thorough, uses the most credits",
}

# Opposite-gender voice: a female voice for a detected-male student, a male
# voice for a detected-female student. Speaker names are from Sarvam's
# bulbul:v3 roster. Detection is pitch-based (see app.services.audio_qa.
# detect_gender_from_pitch) — a coarse, admittedly error-prone heuristic
# accepted deliberately for a first pass rather than not offering it at all.
OPPOSITE_GENDER_SPEAKER = {"male": "priya", "female": "shubh"}


def assistant_voice_gender(student: Student) -> str:
    """
    Which gender the tutor's own voice reads as — needed so a Hindi
    translation can use matching grammatically-gendered verb forms (Hindi
    conjugates first-person verbs by the speaker's gender), not just for
    picking the TTS speaker name. Falls back to the configured default
    speaker's gender when the student's own gender hasn't been detected yet.
    """
    speaker = OPPOSITE_GENDER_SPEAKER.get(student.gender, settings.sarvam_tts_speaker)
    return "male" if speaker == "shubh" else "female"


def _level_downgrade_offer(student: Student, fraction_before: float | None, fraction_after: float | None) -> tuple[int | None, str | None]:
    """
    (offered_level, nudge_key) for a NEW 50%/75% trial-credit downgrade
    offer this turn crossed into — see app.business_rules.
    TUTOR_LEVEL_NUDGE_OFFERS and Student.level_nudges_sent. (None, None)
    when nothing new to offer: no threshold crossed, this milestone was
    already offered before (level_nudges_sent), or the student is already
    at/below the level that milestone would offer (nothing cheaper to
    suggest — e.g. a student who already switched to Level 1 shouldn't be
    re-offered Level 2 at 75%).
    """
    if fraction_after is None:
        return None, None
    before = fraction_before if fraction_before is not None else 0.0
    already_sent = set(student.level_nudges_sent or [])
    for threshold, offered_level in sorted(TUTOR_LEVEL_NUDGE_OFFERS.items()):
        key = str(int(threshold * 100))
        if before < threshold <= fraction_after and key not in already_sent and offered_level < student.tutor_level:
            return offered_level, key
    return None, None


def _weekly_usage_snapshot(db: Session, student: Student) -> dict[str, int]:
    """One query for every soft-capped service's usage this week, instead
    of a separate round-trip per feature."""
    all_services = [service for cfg in FEATURE_LIMITS.values() for service in cfg["services"]]
    return cost_tracker.get_usage_counts_by_service(db, student.id, all_services, "week")


def _usage_status(feature: str, weekly_counts: dict[str, int]) -> tuple[int, int]:
    cfg = FEATURE_LIMITS[feature]
    used = sum(weekly_counts.get(service, 0) for service in cfg["services"])
    return used, cfg["max"]


@dataclass
class ChatTurnResult:
    reply_text: str  # final text — citation/usage-notice already appended, translated if needed
    detected_lang: str = "en-IN"
    did_answer_via_llm: bool = False
    image_prompt: str | None = None  # set only if the tutor decided a diagram was warranted
    wants_audio_reply: bool = False  # set only if the student explicitly asked for a voice reply
    menu_buttons: list[str] | None = None  # present only for the "menu" intent; channels without button UI can ignore
    video: dict | None = None  # {"title", "url"} — kept OUT of reply_text so voice-reply TTS never reads a raw URL aloud
    level_offer: int | None = None  # set only when a NEW 50%/75% downgrade nudge fired this turn — see _level_downgrade_offer


async def process_message(db: Session, student: Student, message_text: str) -> ChatTurnResult:
    """
    The one place that decides how the tutor responds to a message, shared
    by every channel. Owns its own chat_history read/write, intent
    classification and routing (quiz/menu/progress/credit_usage/referral/
    teacher_help/regular tutoring), translation, citation/video appending,
    usage-threshold warnings, and habit/referral milestone checks — a
    caller just needs an already-resolved Student and the plain-text
    message (voice/photo/document input must already be
    transcribed/OCR'd/extracted by the caller before this point).
    """
    # Computed BEFORE this message is saved, so "days since last message"
    # reflects the prior session, not this one — otherwise it would always
    # read as 0 days once this message is in chat_history.
    welcome_back_note = get_welcome_back_note(db, student.id)
    apply_active_document_pin(db, student, message_text)

    # Captured before this turn's own billing lands, so the end-of-function
    # 50/75/90% reminder can tell whether THIS turn carried the student
    # across a threshold.
    usage_fraction_before = cost_tracker.get_usage_fraction(db, student)

    prior_rows = (
        db.query(ChatHistory)
        .filter(ChatHistory.student_id == student.id)
        .order_by(ChatHistory.created_at.desc())
        .limit(HISTORY_TURNS)
        .all()
    )
    history = [{"role": row.role, "content": row.message} for row in reversed(prior_rows)]

    incoming_row = ChatHistory(student_id=student.id, role="user", message=message_text, agent="tutor")
    db.add(incoming_row)
    db.commit()
    db.refresh(incoming_row)

    logger.info(
        "turn start student_id=%s pending_field=%s active_quiz_id=%s text=%r",
        student.id, student.pending_profile_field, student.active_quiz_id, message_text[:200],
    )

    # Resolves "quiz on the same"/"quiz on this" — prefers last_discussed_
    # topic (updated on every real tutoring turn) over TopicProgress, which
    # is only written when a scored check question is evaluated.
    last_topic_row = (
        db.query(TopicProgress.topic)
        .filter(TopicProgress.student_id == student.id)
        .order_by(TopicProgress.created_at.desc())
        .first()
    )
    last_topic_context = student.last_discussed_topic or (last_topic_row[0] if last_topic_row else None)
    last_assistant_message = next((h["content"] for h in reversed(history) if h["role"] == "assistant"), None)

    # Keyword-only candidate chunks (cheap Postgres full-text recall, no
    # external call) are fetched unconditionally so classify_intent's
    # single call can also judge their relevance (relevant_excerpts) — see
    # app.services.retrieval's module docstring for why that judgment rides
    # along here instead of a separate dedicated call. Semantic (Voyage)
    # recall is deliberately NOT included at this stage — it costs a live,
    # rate-limited API call, and most branches below (quiz answers, menu,
    # progress, credit_usage, referral, teacher_help, mock_test, quiz_topic)
    # never use candidate_chunks at all. It's fetched instead only in the
    # final `else` (LLM-answer) branch, the one branch that actually reads
    # relevant_chunks — see there.
    candidate_chunks = fetch_candidate_chunks(db, message_text, student.class_, student.board)
    classification = await classify_intent(
        message_text, last_discussed_topic=last_topic_context, last_assistant_message=last_assistant_message,
        candidate_chunks=candidate_chunks,
    )
    cost_tracker.record_claude_usage(
        db, classification.llm_result.model, classification.llm_result.input_tokens,
        classification.llm_result.output_tokens, student.id,
        cache_write_tokens=classification.llm_result.cache_write_tokens,
        cache_read_tokens=classification.llm_result.cache_read_tokens,
    )
    if classification.relevance_llm_result is not None:
        cost_tracker.record_claude_usage(
            db, classification.relevance_llm_result.model, classification.relevance_llm_result.input_tokens,
            classification.relevance_llm_result.output_tokens, student.id,
            cache_write_tokens=classification.relevance_llm_result.cache_write_tokens,
            cache_read_tokens=classification.relevance_llm_result.cache_read_tokens,
        )
    intent = CANONICAL_COMMAND_INTENT.get(message_text.strip().lower(), classification.intent)
    # Confirmed live: classify_intent occasionally misclassifies a genuine
    # tutoring question as "menu" (e.g. "Explain the process of
    # photosynthesis" right after a greeting reply that itself ended in a
    # question, and later "What is the water cycle?" in the same
    # situation). Logged here (raw classifier output vs. the possibly-
    # overridden final intent, plus the last assistant turn for context)
    # so a future occurrence has a real debug trail instead of having to
    # be caught live again.
    if classification.intent in ("menu", "quiz_stop") or intent != classification.intent:
        logger.info(
            "intent classification student_id=%s text=%r raw_intent=%s final_intent=%s last_assistant=%r",
            student.id, message_text[:200], classification.intent, intent,
            (last_assistant_message or "")[:200],
        )
    quiz_topic_request = classification.quiz_topic
    # classify_intent's own prompt already lists "mock test" as a literal
    # example of wants_mock_test=yes — confirmed live it still missed the
    # bare phrase itself, silently falling through to generic tutoring
    # (an improvised, unscored fake quiz question) instead of the real
    # scored mock_test flow. Exact-match only (not a substring check) —
    # confirmed live that a broader "mock test" in message_text.lower()
    # check was a real regression: "What is a mock test and how does it
    # work?" (a genuine question ABOUT mock tests, not a request for one)
    # incorrectly started a real scored test. Same discipline as
    # CANONICAL_COMMAND_INTENT above: only override for phrasing
    # unambiguous enough that the model's judgment isn't needed at all.
    mock_test_request = classification.wants_mock_test or message_text.strip().lower() in (
        "mock test", "give me a mock test", "start a mock test", "start mock test",
    )

    did_answer_via_llm = False
    image_prompt = None
    video_query = None
    citation = None
    wants_audio_reply = False
    menu_buttons = None

    if student.pending_profile_field == "class_confirm" and (quiz_topic_request or mock_test_request):
        # An explicit new request in the same message should win over
        # resolving the pending class-update nudge — treat it as an
        # implicit decline (leave the class as-is).
        student.pending_profile_field = None
        student.suggested_class = None
        db.commit()

    if intent == "menu" and not student.active_quiz_id:
        # Free UI affordance — returns immediately without touching
        # chat_history/credits beyond the classification call above, same
        # as every channel this ladder branch has always behaved.
        if not prior_rows:
            # A student's very first message ever — "My Progress" and
            # "Refer a Friend" are dead ends with zero history behind them.
            # Skip the returning-user menu entirely and make the first
            # touch feel like meeting a tutor, not a support bot.
            first_name = student.name if student.name and student.name != "New Student" else None
            greeting = f"Hi {first_name}! 👋" if first_name else "Hi! 👋"
            # Only advertise a capability the student actually has — a
            # first "wow" moment that turns out broken is worse than not
            # mentioning it at all.
            capability_hints = []
            if student.has_feature("ocr"):
                capability_hints.append("send a photo of a tricky homework question")
            if student.has_feature("voice"):
                capability_hints.append("send a voice note")
            if student.has_feature("documents"):
                capability_hints.append("share a PDF or Word file of your homework")
            if student.has_feature("youtube_videos"):
                capability_hints.append("ask for a video explanation")
            if not capability_hints:
                capability_sentence = ""
            elif len(capability_hints) == 1:
                capability_sentence = f" You can also {capability_hints[0]}."
            else:
                capability_sentence = f" You can also {', '.join(capability_hints[:-1])}, or {capability_hints[-1]}."
            reply_text = (
                f"{greeting} I'm your AI tutor — ask me to explain any topic or say \"quiz me on <topic>\" "
                f"to test yourself.{capability_sentence} What would you like to learn today?"
            )
        else:
            # A returning student saying "Hi" after a gap is the single most
            # common re-entry message. welcome_back_note is NOT woven in here
            # — it's prepended once, uniformly for every branch (including
            # this one now), by the shared "if welcome_back_note" step below.
            activity = get_activity_stats(db, student.id)
            weekly_stats = get_student_stats(db, student.id, days=7)
            menu_buttons = list(MENU_BUTTONS_BASE)
            if is_worth_asking_to_refer(activity, weekly_stats["messages_sent"]):
                menu_buttons.append("🎁 Refer a Friend")
            fallback_commands = ", ".join(f"'{MENU_BUTTON_TO_COMMAND[b]}'" for b in menu_buttons)
            reply_text = (
                f"Hi! Just ask me anything to start learning, or pick one of these:\n\n"
                f"Or reply with {fallback_commands} or anything else you want to work on."
            )
        # Same translation treatment as every other branch below (menu used
        # to return early here, skipping it entirely — a Hindi-speaking
        # student on any channel always got this in English).
        detected_lang = student.preferred_language or "en-IN"
    elif student.active_quiz_id and intent == "quiz_stop":
        reply_text = stop_quiz(db, student)
        detected_lang = student.preferred_language or "en-IN"
    elif intent == "progress":
        # Computed directly from real TopicProgress rows — no risk of the
        # model inventing stats.
        stats = get_student_stats(db, student.id)
        activity = get_activity_stats(db, student.id)
        coverage = get_chapter_coverage(db, student)
        reply_text = format_progress_message(stats, activity, coverage)
        detected_lang = student.preferred_language or "en-IN"
    elif intent == "credit_usage":
        # Same "no model-invented numbers" principle as progress above.
        if cost_tracker.is_unlimited_active(student):
            status = cost_tracker.get_unlimited_usage_status(db, student)
            usage_lines = "\n".join(
                f"- This {period}: ₹{v['spent']:.2f} of ₹{v['cap']:.0f} used" for period, v in status.items()
            )
            wallet_balance = cost_tracker.get_balance(db, student.id)
            overage_note = (
                f"\n\nYou also have ₹{wallet_balance:.2f} in usage credits available if you go past "
                f"your plan's included usage." if wallet_balance > 0 else ""
            )
            reply_text = f"You're on the unlimited plan ✨\n\n{usage_lines}{overage_note}"
        else:
            balance = cost_tracker.get_balance(db, student.id)
            reply_text = (
                f"💳 Your current AI credit balance: ₹{balance:.2f}\n\n"
                f"Reply \"top up credits\" anytime to add more."
            )
        detected_lang = student.preferred_language or "en-IN"
    elif intent == "change_level":
        command_text = message_text.strip().lower()
        target_level = LEVEL_COMMAND_TARGET.get(command_text)
        if command_text == "stay on current level":
            student.pending_level_offer = None
            db.commit()
            reply_text = f"Got it — staying on Level {student.tutor_level}."
        elif target_level:
            student.tutor_level = target_level
            student.pending_level_offer = None
            db.commit()
            reply_text = f"Done — you're now on {TUTOR_LEVEL_LABELS[target_level]}. Reply \"change level\" anytime to switch again."
        else:
            options = "\n".join(f"- {label}" for label in TUTOR_LEVEL_LABELS.values())
            reply_text = (
                f"You're currently on Level {student.tutor_level}.\n\n{options}\n\n"
                f"Reply \"level 1\", \"level 2\", \"level 3\", or \"level 4\" to switch."
            )
        detected_lang = student.preferred_language or "en-IN"
    elif intent == "referral":
        # Generated lazily here too (not just at signup) so students
        # created before this feature shipped still get a code the first
        # time they ask.
        if not student.referral_code:
            student.referral_code = generate_referral_code(student.id)
            db.commit()
        reply_text = (
            f"Share your code with a friend — when they message me for the first time and start "
            f"asking questions, you'll get ₹{REFERRAL_SIGNUP_BONUS:.0f} in AI credits "
            f"(up to ₹{REFERRAL_LIFETIME_CAP:.0f} total)! 🎉\n\n"
            f"Your code: *{student.referral_code}*\n"
            f"Tell them to just message me and mention this code in their first message."
        )
        detected_lang = student.preferred_language or "en-IN"
    elif intent == "teacher_help":
        # Distinct from the automatic hint-streak escalation further below
        # — this is the student explicitly asking, not a system-detected
        # struggle pattern.
        teacher_recipients = get_escalation_recipients(db, student.centre_id)
        for recipient in teacher_recipients:
            await _notify_recipient(recipient.phone, _requested_help_message(student.name))
        reply_text = (
            "I've let your teacher know you'd like some help! 🙋 They'll reach out soon. "
            "What else can I help you with in the meantime?"
            if teacher_recipients
            else "You're not linked to a school on our records, so I can't reach a teacher for you right now. "
                 "What else can I help you with?"
        )
        detected_lang = student.preferred_language or "en-IN"
    elif student.active_quiz_id:
        reply_text = await handle_quiz_answer(db, student, message_text, classification.quiz_skip)
        detected_lang = student.preferred_language or "en-IN"
    elif mock_test_request:
        mock_topic = classification.mock_test_topic or "a mixed review covering everything we've discussed so far"
        reply_text = await start_mock_test(db, student, mock_topic)
        detected_lang = student.preferred_language or "en-IN"
    elif quiz_topic_request:
        reply_text = await start_quiz(db, student, quiz_topic_request)
        detected_lang = student.preferred_language or "en-IN"
    elif (
        db.query(ChatHistory.id)
        .filter(ChatHistory.student_id == student.id, ChatHistory.role == "user", ChatHistory.id > incoming_row.id)
        .first()
        is not None
    ):
        # A newer message from this same student already arrived while this
        # one was being processed (e.g. several rapid-fire texts sent
        # within seconds of each other) — skip generating a reply for this
        # stale message entirely; it's already in chat_history, so the
        # newer message's own reply sees it as context and can respond to
        # the whole exchange at once.
        return ChatTurnResult(reply_text="")
    else:
        did_answer_via_llm = True

        # Whether this message actually answers a pending onboarding
        # question (name/class/board/school) or the class-update nudge is
        # decided by the LLM itself below, not a local regex heuristic — a
        # plain word-matching check previously mis-swallowed mixed messages
        # discarding real content entirely.
        pending_class_confirm = student.suggested_class if student.pending_profile_field == "class_confirm" else None
        pending_profile_field = (
            student.pending_profile_field if student.pending_profile_field != "class_confirm" else None
        )

        # Surface topics this student has previously gotten wrong, so the
        # tutor can naturally revisit them.
        weak_topic_rows = (
            db.query(TopicProgress.topic)
            .filter(TopicProgress.student_id == student.id, TopicProgress.is_correct.is_(False))
            .order_by(TopicProgress.created_at.desc())
            .limit(WEAK_TOPICS_LIMIT)
            .all()
        )
        weak_topics = list(dict.fromkeys(row[0] for row in weak_topic_rows))

        relevant_chunks = [
            candidate_chunks[i - 1] for i in classification.relevant_excerpts if 1 <= i <= len(candidate_chunks)
        ]
        # Semantic recall (a live Voyage call) is only worth paying for once
        # a turn has actually reached the LLM-answer branch — see the note
        # by the keyword-only fetch above. classify_intent already judged
        # the keyword candidates' relevance; semantic candidates arrive
        # after that call, so they're appended directly (already ranked by
        # embedding similarity) rather than run through a second relevance
        # judgment, up to the same overall candidate limit.
        if len(relevant_chunks) < MAX_CANDIDATES:
            semantic_chunks = await fetch_semantic_candidates(db, message_text, student.class_, student.board)
            seen = {chunk.content for chunk in relevant_chunks}
            for chunk in semantic_chunks:
                if len(relevant_chunks) >= MAX_CANDIDATES:
                    break
                if chunk.content in seen:
                    continue
                seen.add(chunk.content)
                relevant_chunks.append(chunk)
        # Capped to MAX_CHUNKS (not MAX_CANDIDATES) for what actually goes
        # into the LLM's context — every chunk here costs real input tokens
        # on every single call since this part of the prompt can't be
        # cached (see the note by tutor_agent.respond below), unlike the
        # much larger static instructional prompt. Keyword-matched chunks
        # (already LLM-relevance-judged) come first in the list, so the
        # highest-trust ones are kept.
        relevant_chunks = relevant_chunks[:MAX_CHUNKS]
        result = await tutor_agent.respond(
            student.as_profile_dict(), message_text, history, weak_topics,
            image_generation_enabled=student.has_feature("image_generation"),
            voice_enabled=student.has_feature("voice"),
            video_enabled=student.has_feature("youtube_videos"),
            active_document_text=student.active_document_text,
            pending_class_confirm=pending_class_confirm,
            pending_profile_field=pending_profile_field,
            retrieved_chunks=relevant_chunks,
            model=TUTOR_LEVEL_MODELS.get(student.tutor_level, TUTOR_LEVEL_MODELS[DEFAULT_TUTOR_LEVEL]),
        )
        reply_text = result["reply"]
        detected_lang = result["lang"]
        image_prompt = result["image_prompt"]
        video_query = result["video_query"]
        citation = result["citation"]
        wants_audio_reply = result["wants_audio_reply"]

        if pending_class_confirm:
            if result["class_confirm"] is True:
                student.class_ = pending_class_confirm
            student.pending_profile_field = None
            student.suggested_class = None
            db.commit()
        elif pending_profile_field:
            profile_answer = result["profile_answer"]
            # Rejects an implausible extraction (e.g. the whole raw message
            # instead of just the clean answer) rather than trusting it
            # verbatim — see is_plausible_profile_answer's docstring. The
            # field is left blank either way, so next_missing_field asks
            # again later instead of a bad value becoming permanent.
            if profile_answer and is_plausible_profile_answer(pending_profile_field, profile_answer):
                setattr(student, pending_profile_field, profile_answer)
            student.pending_profile_field = None
            db.commit()

        usage = result["usage"]
        cost_tracker.record_claude_usage(
            db, usage["main_model"], usage["main_input_tokens"], usage["main_output_tokens"], student.id,
            cache_write_tokens=usage["main_cache_write_tokens"], cache_read_tokens=usage["main_cache_read_tokens"],
        )
        cost_tracker.record_claude_usage(
            db, usage["classify_model"], usage["classify_input_tokens"], usage["classify_output_tokens"], student.id,
            cache_write_tokens=usage["classify_cache_write_tokens"], cache_read_tokens=usage["classify_cache_read_tokens"],
        )

        # Soft weekly caps: voice/image/video are supplements, not the core
        # paid feature, so hitting their cap just skips that extra
        # (falling back to the text reply, which stands on its own) rather
        # than blocking the whole turn.
        weekly_counts = _weekly_usage_snapshot(db, student)
        if wants_audio_reply:
            used, limit = _usage_status("voice", weekly_counts)
            if used >= limit:
                wants_audio_reply = False
        if image_prompt:
            used, limit = _usage_status("image_generation", weekly_counts)
            if used >= limit:
                image_prompt = None
        if video_query:
            used, limit = _usage_status("youtube_videos", weekly_counts)
            if used >= limit:
                video_query = None

        if detected_lang != student.preferred_language:
            student.preferred_language = detected_lang
            db.commit()

        # Academic-integrity signal for the teacher digest — how often the
        # tutor gave a hint vs. a full worked solution.
        if result["topic"]:
            student.last_discussed_topic = result["topic"]
            db.commit()

        if result["solved_directly"] is True:
            student.direct_solutions_count = (student.direct_solutions_count or 0) + 1
            db.commit()
        elif result["solved_directly"] is False:
            student.hints_given_count = (student.hints_given_count or 0) + 1
            db.commit()

        should_escalate = _record_hint_outcome(student, result["evaluated"], result["correct"])
        if should_escalate:
            student.consecutive_unresolved_hints = 0
            db.commit()
            escalation_message = format_escalation_message(student.name, result["topic"])
            for recipient in get_escalation_recipients(db, student.centre_id):
                await _notify_recipient(recipient.phone, escalation_message)
            if not result["closing"]:
                reply_text = f"{reply_text}\n\nBy the way, I've let your teacher know you might want a hand with this — they'll reach out soon. 🙋"

        if result["evaluated"]:
            last_assistant_turn = next((h["content"] for h in reversed(history) if h["role"] == "assistant"), None)
            db.add(TopicProgress(
                student_id=student.id, topic=result["topic"] or "unknown",
                question_text=last_assistant_turn, given_answer=message_text, is_correct=result["correct"],
            ))
            db.commit()

        # Track a run of consecutive off-level questions (e.g. registered as
        # class 8 but repeatedly asking Class 12 content) and, after a real
        # pattern rather than a single one-off question, suggest updating
        # their registered class.
        if result["off_level_class"] and result["off_level_class"] != student.class_:
            student.off_level_count = (student.off_level_count or 0) + 1
        else:
            student.off_level_count = 0
        db.commit()

        if (
            result["off_level_class"]
            and not student.is_staff_profile
            and student.off_level_count >= OFF_LEVEL_SUGGEST_THRESHOLD
            and not student.pending_profile_field
            and not reply_text.rstrip().endswith("?")
            and not result["closing"]
        ):
            level = result["off_level_class"]
            reply_text = (
                f"{reply_text}\n\nBy the way, I've noticed you've been asking Class {level} questions — "
                f"want me to update your registered class to {level}?"
            )
            student.pending_profile_field = "class_confirm"
            student.suggested_class = level
            student.off_level_count = 0
            db.commit()

        user_message_count = (
            db.query(ChatHistory).filter(ChatHistory.student_id == student.id, ChatHistory.role == "user").count()
        )
        # Only held back for a goodbye, not for a reply that already ends
        # with the tutor's own question — confirmed live that the tutor's
        # replies end with a pedagogical check-question near-universally by
        # design, so gating on "doesn't already end in ?" meant this almost
        # never actually fired: a student who volunteered their name/class
        # unprompted got a warm "noted!"-sounding reply that never actually
        # persisted anything, silently, every time. A rare double-question
        # message is a far smaller cost than onboarding never completing.
        # A teacher's own "My AI Tutor" account (is_staff_profile=True) has
        # no real class/board/school of its own — confirmed live, a teacher
        # asking a plain question got "which class are you in?" tacked onto
        # the answer, a question that only makes sense for an actual
        # student profile. Skip the whole onboarding nudge ladder for staff
        # profiles rather than asking fields that will never legitimately
        # get filled in.
        if not student.is_staff_profile and should_ask_this_turn(user_message_count) and not result["closing"]:
            missing = next_missing_field(student)
            if missing:
                field, question = missing
                reply_text = f"{reply_text}\n\n{question}"
                student.pending_profile_field = field
                db.commit()

    if welcome_back_note:
        reply_text = f"{welcome_back_note}\n\n{reply_text}"

    # Saved in English (what Claude actually said) so future turns give the
    # model a consistent conversation history to reason over. Deliberately
    # BEFORE the usage-threshold notice below: that notice is a system
    # aside, not tutoring content, and saving it into history would let a
    # future turn see it as if it were the tutor's own prior statement and
    # potentially echo it back.
    db.add(ChatHistory(student_id=student.id, role="assistant", message=reply_text, agent="tutor"))
    db.commit()
    logger.info(
        "turn end student_id=%s via_llm=%s active_quiz_id=%s reply=%r",
        student.id, did_answer_via_llm, student.active_quiz_id, reply_text[:200],
    )

    # This turn's own AI usage just moved the needle on the student's
    # current usage cap — warn at 50/75/90% so hitting it is never a
    # surprise. Only relevant when a real LLM call actually happened this
    # turn (not the progress/credit_usage/referral/quiz branches, which
    # never touch billing).
    level_offer = None
    if did_answer_via_llm:
        usage_fraction_after = cost_tracker.get_usage_fraction(db, student)
        usage_notice = cost_tracker.usage_threshold_notice(usage_fraction_before, usage_fraction_after)
        if usage_notice:
            offered_level, nudge_key = _level_downgrade_offer(student, usage_fraction_before, usage_fraction_after)
            if offered_level:
                student.pending_level_offer = offered_level
                student.level_nudges_sent = list(student.level_nudges_sent or []) + [nudge_key]
                db.commit()
                level_offer = offered_level
                usage_notice = (
                    f"{usage_notice} Want your credits to go further? Switch to {TUTOR_LEVEL_LABELS[offered_level]} "
                    f"— reply \"level {offered_level}\" anytime, or just keep chatting to stay as you are."
                )
            reply_text = f"{reply_text}\n\n{usage_notice} Reply \"credit usage\" anytime for the full breakdown."

    # Translate into the student's language for what actually gets sent.
    # Claude (Haiku) does this first — cheap token cost instead of Sarvam
    # Mayura's per-character billing. Falls back to Sarvam only if the
    # Claude call fails, and to the plain English reply if not needed or
    # both translation paths fail.
    outgoing_text = reply_text
    if detected_lang and not detected_lang.startswith("en"):
        translated_result = await translate_with_claude(
            reply_text, detected_lang, speaker_gender=assistant_voice_gender(student), student_state=student.state
        )
        if translated_result:
            outgoing_text = translated_result.text
            cost_tracker.record_claude_usage(
                db, translated_result.model, translated_result.input_tokens, translated_result.output_tokens,
                student.id, cache_write_tokens=translated_result.cache_write_tokens,
                cache_read_tokens=translated_result.cache_read_tokens,
            )
        else:
            translated = await translate_text(reply_text, "en-IN", detected_lang)
            if translated:
                outgoing_text = translated
                cost_tracker.record_char_usage(db, "sarvam_translate", len(reply_text), student.id)

    # Appended AFTER translation, not before — a chapter title is a proper
    # noun, and running it through the translation pass risked mangling it
    # into awkward Hindi rather than leaving it as-is. `citation` is None
    # unless this turn actually went through the tutor branch AND
    # grounding fired.
    if citation:
        outgoing_text = f"{outgoing_text}\n\n{citation}"

    video_result = None
    if video_query:
        video = await find_best_video(video_query, student_language_code=detected_lang, student_class=student.class_)
        cost_tracker.record_youtube_search(db, student.id)
        if video:
            # Deliberately kept OUT of outgoing_text (rather than appended
            # like the citation above) — outgoing_text also feeds
            # voice-reply TTS synthesis further up the caller's own
            # delivery chain, and a spoken-aloud URL is useless. Callers
            # append/send this separately: WhatsApp as its own follow-up
            # message (no native rich video embed), web/app can fold it in.
            video_result = {"title": video["title"], "url": video["url"]}

    if did_answer_via_llm:
        # Deliberately last, after outgoing_text is fully built — these
        # send their own separate WhatsApp message on top of whatever
        # channel this reply is being returned through, and used to run
        # mid-function, adding an extra blocking WhatsApp round-trip before
        # the actual reply was ready.
        await evaluate_referral_milestones(db, student)
        await evaluate_habit_milestones(db, student)

    return ChatTurnResult(
        reply_text=outgoing_text, detected_lang=detected_lang, did_answer_via_llm=did_answer_via_llm,
        image_prompt=image_prompt, wants_audio_reply=wants_audio_reply, menu_buttons=menu_buttons,
        video=video_result, level_offer=level_offer,
    )


async def _notify_recipient(phone: str, message: str) -> None:
    from app.services.whatsapp_client import send_whatsapp_message
    await send_whatsapp_message(phone, message)


def _requested_help_message(student_name: str) -> str:
    from app.services.escalation import format_student_requested_help_message
    return format_student_requested_help_message(student_name)


def _record_hint_outcome(student: Student, evaluated: bool, correct: bool | None) -> bool:
    from app.services.escalation import record_hint_outcome
    return record_hint_outcome(student, evaluated, correct)
