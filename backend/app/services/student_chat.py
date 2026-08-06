from sqlalchemy.orm import Session

from app.models.core import ChatHistory, Student, TopicProgress
from app.services import cost_tracker
from app.services.document_client import apply_active_document_pin
from app.services.habit import evaluate_habit_milestones
from app.services.intent_classifier import classify_intent
from app.services.llm_client import translate_with_claude
from app.services.progress_report import get_welcome_back_note
from app.services.quiz_flow import handle_quiz_answer, start_mock_test, start_quiz, stop_quiz
from app.services.retrieval import fetch_candidate_chunks
from app.services.referral import evaluate_referral_milestones
from app.services.youtube_client import find_best_video
from app.agents.tutor_agent import TutorAgent

HISTORY_TURNS = 12
WEAK_TOPICS_LIMIT = 5

tutor_agent = TutorAgent()


async def process_web_message(db: Session, student: Student, message_text: str) -> str:
    """
    The student web/app chat endpoint — shares the same TutorAgent, credit
    ledger, weak-topic surfacing, referral milestones, translation logic,
    real scored quiz flow, AND active-document pinning as WhatsApp (see
    app.services.quiz_flow, app.services.document_client), so a student's
    experience/progress is consistent regardless of which channel they use.
    `message_text` is always plain text by the time it gets here — voice
    notes and photos are transcribed/OCR'd by the caller (see
    app.routers.student_app's send-voice/send-image endpoints) before being
    passed in, exactly like app.routers.whatsapp does; this function itself
    doesn't know or care whether the text was typed, spoken, or read off a
    photo.
    """
    # Computed BEFORE this message is saved, so "days since last message"
    # reflects the prior session, not this one (same reasoning as the
    # WhatsApp path in app.routers.whatsapp).
    welcome_back_note = get_welcome_back_note(db, student.id)
    apply_active_document_pin(db, student, message_text)

    # Captured before this turn's own billing lands, so the end-of-function
    # 50/75/90% reminder can tell whether THIS turn carried the student
    # across a threshold — same mechanism as app.routers.whatsapp (see
    # cost_tracker.get_usage_fraction for what "usage" means per plan).
    usage_fraction_before = cost_tracker.get_usage_fraction(db, student)

    prior_rows = (
        db.query(ChatHistory)
        .filter(ChatHistory.student_id == student.id)
        .order_by(ChatHistory.created_at.desc())
        .limit(HISTORY_TURNS)
        .all()
    )
    history = [{"role": row.role, "content": row.message} for row in reversed(prior_rows)]

    db.add(ChatHistory(student_id=student.id, role="user", message=message_text, agent="tutor"))
    db.commit()

    # Resolves "quiz on the same"/"quiz on this" — same reasoning as the
    # WhatsApp path (app.routers.whatsapp).
    last_topic_row = (
        db.query(TopicProgress.topic)
        .filter(TopicProgress.student_id == student.id)
        .order_by(TopicProgress.created_at.desc())
        .first()
    )
    last_topic_context = student.last_discussed_topic or (last_topic_row[0] if last_topic_row else None)
    last_assistant_message = next((h["content"] for h in reversed(history) if h["role"] == "assistant"), None)

    # Candidate chunks (cheap Postgres full-text recall, no LLM cost) are
    # fetched unconditionally here so classify_intent's single call can
    # also judge their relevance (relevant_excerpts) — see
    # app.services.retrieval's module docstring for why that judgment
    # rides along in this call instead of a separate dedicated one.
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

    detected_lang = student.preferred_language or "en-IN"
    result = None
    citation = None
    video_query = None
    if student.active_quiz_id and classification.intent == "quiz_stop":
        reply_text = stop_quiz(db, student)
    elif student.active_quiz_id:
        reply_text = await handle_quiz_answer(db, student, message_text, classification.quiz_skip)
    elif classification.wants_mock_test:
        mock_topic = classification.mock_test_topic or "a mixed review covering everything we've discussed so far"
        reply_text = await start_mock_test(db, student, mock_topic)
    elif classification.quiz_topic:
        reply_text = await start_quiz(db, student, classification.quiz_topic)
    else:
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
        result = await tutor_agent.respond(
            student.as_profile_dict(), message_text, history, weak_topics,
            image_generation_enabled=False, voice_enabled=False,
            video_enabled=student.has_feature("youtube_videos"),
            active_document_text=student.active_document_text, retrieved_chunks=relevant_chunks,
        )
        reply_text = result["reply"]
        detected_lang = result["lang"]
        usage = result["usage"]
        cost_tracker.record_claude_usage(
            db, usage["main_model"], usage["main_input_tokens"], usage["main_output_tokens"], student.id,
            cache_write_tokens=usage["main_cache_write_tokens"], cache_read_tokens=usage["main_cache_read_tokens"],
        )
        cost_tracker.record_claude_usage(
            db, usage["classify_model"], usage["classify_input_tokens"], usage["classify_output_tokens"], student.id,
            cache_write_tokens=usage["classify_cache_write_tokens"], cache_read_tokens=usage["classify_cache_read_tokens"],
        )

        if result["solved_directly"] is True:
            student.direct_solutions_count = (student.direct_solutions_count or 0) + 1
            db.commit()
        elif result["solved_directly"] is False:
            student.hints_given_count = (student.hints_given_count or 0) + 1
            db.commit()

        if result["evaluated"]:
            last_assistant_turn = next((h["content"] for h in reversed(history) if h["role"] == "assistant"), None)
            db.add(TopicProgress(
                student_id=student.id, topic=result["topic"] or "unknown",
                question_text=last_assistant_turn, given_answer=message_text, is_correct=result["correct"],
            ))
            db.commit()

        if result["topic"]:
            student.last_discussed_topic = result["topic"]
            db.commit()

        citation = result["citation"]
        video_query = result["video_query"]

    if welcome_back_note:
        reply_text = f"{welcome_back_note}\n\n{reply_text}"

    if detected_lang != student.preferred_language:
        student.preferred_language = detected_lang
        db.commit()

    db.add(ChatHistory(student_id=student.id, role="assistant", message=reply_text, agent="tutor"))
    db.commit()

    outgoing_text = reply_text
    if detected_lang and not detected_lang.startswith("en"):
        translated_result = await translate_with_claude(reply_text, detected_lang)
        if translated_result:
            outgoing_text = translated_result.text
            cost_tracker.record_claude_usage(
                db, translated_result.model, translated_result.input_tokens, translated_result.output_tokens,
                student.id, cache_write_tokens=translated_result.cache_write_tokens,
                cache_read_tokens=translated_result.cache_read_tokens,
            )

    # Appended AFTER translation, not before — a chapter title is a proper
    # noun, and running it through the translation pass risked mangling it
    # into awkward Hindi rather than leaving it as-is (see
    # app.services.retrieval.build_citation_footer).
    if citation:
        outgoing_text = f"{outgoing_text}\n\n{citation}"

    # Same "own follow-up line" treatment as WhatsApp (app.routers.whatsapp)
    # — a real search happens here, not a hypothetical, so this only runs
    # when video_query was actually set (i.e. the tutor decided THIS reply
    # genuinely warranted one — see tutor_agent.py's video prompt). Web/app
    # has no native rich video embed either, so the title+URL is just
    # appended as plain text, same as WhatsApp's own delivery.
    if video_query:
        video = await find_best_video(video_query, student_language_code=detected_lang, student_class=student.class_)
        cost_tracker.record_youtube_search(db, student.id)
        if video:
            outgoing_text = f"{outgoing_text}\n\n📺 {video['title']}\n{video['url']}"

    # This turn's own AI usage just moved the needle on the student's
    # current usage cap — warn at 50%/75%/90% so hitting it is never a
    # surprise, same as app.routers.whatsapp.
    usage_fraction_after = cost_tracker.get_usage_fraction(db, student)
    usage_notice = cost_tracker.usage_threshold_notice(usage_fraction_before, usage_fraction_after)
    if usage_notice:
        outgoing_text = f"{outgoing_text}\n\n{usage_notice}"

    if result is not None:
        # Deliberately last, after outgoing_text is fully built (translation
        # + citation + video + usage notice) and the reply is about to be
        # returned to the caller — same fix as app.routers.whatsapp: these
        # send their own separate WhatsApp message on top of whatever
        # channel this reply is being returned through (web/app/teacher's
        # My AI Tutor), and used to run mid-function, adding an extra
        # blocking WhatsApp round-trip before the actual reply was ready.
        # Still only for a real tutoring turn (result is not None means the
        # else branch above ran), not quiz/mock-test/quiz-stop turns.
        await evaluate_referral_milestones(db, student)
        await evaluate_habit_milestones(db, student)

    return outgoing_text
