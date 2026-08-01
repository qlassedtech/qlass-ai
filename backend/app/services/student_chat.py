from sqlalchemy.orm import Session

from app.models.core import ChatHistory, Student, TopicProgress
from app.services import cost_tracker
from app.services.habit import evaluate_habit_milestones
from app.services.llm_client import translate_with_claude
from app.services.progress_report import get_welcome_back_note
from app.services.referral import evaluate_referral_milestones
from app.agents.tutor_agent import TutorAgent

HISTORY_TURNS = 12
WEAK_TOPICS_LIMIT = 5

tutor_agent = TutorAgent()


async def process_web_message(db: Session, student: Student, message_text: str) -> str:
    """
    v1 of the student web app's chat endpoint — text-only (no voice/image/
    document/quiz handling yet, unlike the WhatsApp path in
    app.routers.whatsapp._handle_message). Shares the same TutorAgent,
    credit ledger, weak-topic surfacing, referral milestones, and
    translation logic as WhatsApp so a student's experience/progress is
    consistent regardless of which channel they use.
    """
    # Computed BEFORE this message is saved, so "days since last message"
    # reflects the prior session, not this one (same reasoning as the
    # WhatsApp path in app.routers.whatsapp — this was previously missing
    # here entirely, so a returning web-app student never got a
    # welcome-back acknowledgment at all).
    welcome_back_note = get_welcome_back_note(db, student.id)

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

    weak_topic_rows = (
        db.query(TopicProgress.topic)
        .filter(TopicProgress.student_id == student.id, TopicProgress.is_correct.is_(False))
        .order_by(TopicProgress.created_at.desc())
        .limit(WEAK_TOPICS_LIMIT)
        .all()
    )
    weak_topics = list(dict.fromkeys(row[0] for row in weak_topic_rows))

    result = await tutor_agent.respond(
        student.as_profile_dict(), message_text, history, weak_topics,
        image_generation_enabled=False, voice_enabled=False, video_enabled=False,
        active_document_text=student.active_document_text,
    )
    reply_text = result["reply"]
    if welcome_back_note:
        reply_text = f"{welcome_back_note}\n\n{reply_text}"
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

    if detected_lang != student.preferred_language:
        student.preferred_language = detected_lang
        db.commit()

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

    await evaluate_referral_milestones(db, student)
    await evaluate_habit_milestones(db, student)

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

    return outgoing_text
