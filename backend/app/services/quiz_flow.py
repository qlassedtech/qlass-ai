from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.core import Answer, Question, Quiz, Student, TopicProgress
from app.services import cost_tracker
from app.services.quiz_service import MOCK_TEST_QUESTION_COUNT, generate_quiz_questions, grade_answer

"""
Shared quiz orchestration (starting a quiz/mock test, grading each answer,
and wrapping up on completion) — extracted out of app.routers.whatsapp so
the student web app (app.services.student_chat) can offer the exact same
real, scored quiz flow instead of duplicating this logic a second time (or
worse, not having it at all — the web app's own UI used to promise "a
quick quiz" with nothing behind it). Any fix here (e.g. the TopicProgress
write-back on a missed answer) automatically applies to both channels.
"""


async def start_quiz(db: Session, student: Student, topic: str) -> str:
    """Starts a short ad-hoc quiz on `topic`. Sets student.active_quiz_id and commits."""
    questions_data, gen_result = await generate_quiz_questions(topic, student.class_, board=student.board)
    cost_tracker.record_claude_usage(
        db, gen_result.model, gen_result.input_tokens, gen_result.output_tokens, student.id,
        cache_write_tokens=gen_result.cache_write_tokens, cache_read_tokens=gen_result.cache_read_tokens,
    )
    if not questions_data:
        return (
            "Sorry, I couldn't put together a quiz on that right now — want to try a different "
            "topic, or just ask me questions directly?"
        )
    quiz = Quiz(student_id=student.id, title=topic)
    db.add(quiz)
    db.commit()
    db.refresh(quiz)
    for q in questions_data:
        db.add(Question(
            quiz_id=quiz.id, question_type=q.get("question_type", "short_answer"),
            question_text=q["question"], correct_answer=q["answer"],
        ))
    db.commit()
    student.active_quiz_id = quiz.id
    db.commit()
    return (
        f"Great, let's do a {len(questions_data)}-question quiz on *{topic}*! 📝\n\n"
        f"Question 1/{len(questions_data)}: {questions_data[0]['question']}"
    )


async def start_mock_test(db: Session, student: Student, topic: str) -> str:
    """Starts a longer, timed board-exam style mock test on `topic`. Sets student.active_quiz_id and commits."""
    questions_data, gen_result = await generate_quiz_questions(
        topic, student.class_, num_questions=MOCK_TEST_QUESTION_COUNT, board=student.board,
    )
    cost_tracker.record_claude_usage(
        db, gen_result.model, gen_result.input_tokens, gen_result.output_tokens, student.id,
        cache_write_tokens=gen_result.cache_write_tokens, cache_read_tokens=gen_result.cache_read_tokens,
    )
    if not questions_data:
        return (
            "Sorry, I couldn't put together a mock test right now — want to try a specific topic, "
            "or just ask me questions directly?"
        )
    quiz = Quiz(student_id=student.id, is_mock_test=True, title=topic)
    db.add(quiz)
    db.commit()
    db.refresh(quiz)
    for q in questions_data:
        db.add(Question(
            quiz_id=quiz.id, question_type=q.get("question_type", "short_answer"),
            question_text=q["question"], correct_answer=q["answer"],
        ))
    db.commit()
    student.active_quiz_id = quiz.id
    db.commit()
    return (
        f"🎓 Let's do a {len(questions_data)}-question mock test! Take your time, but I'll note "
        f"how long it takes so you get a sense of your exam pace.\n\n"
        f"Question 1/{len(questions_data)}: {questions_data[0]['question']}"
    )


def stop_quiz(db: Session, student: Student) -> str:
    student.active_quiz_id = None
    db.commit()
    return "No problem, quiz stopped! What else can I help you with?"


async def handle_quiz_answer(db: Session, student: Student, message_text: str, quiz_skip: bool) -> str:
    """
    Treats message_text as the answer to the student's current quiz
    question — grades it (or marks it skipped), advances to the next
    question, or wraps up the quiz and clears active_quiz_id if this was
    the last one. Returns the reply text; the caller is responsible for
    sending/returning it and for translation, same as any other reply.
    """
    quiz_questions = db.query(Question).filter(Question.quiz_id == student.active_quiz_id).order_by(Question.id).all()
    answered_count = (
        db.query(Answer).filter(Answer.question_id.in_([q.id for q in quiz_questions])).count()
        if quiz_questions else 0
    )
    if not quiz_questions or answered_count >= len(quiz_questions):
        # Shouldn't normally happen (quiz should already have ended), but guard anyway.
        student.active_quiz_id = None
        db.commit()
        return "Looks like that quiz already wrapped up! What else can I help you with?"

    current_question = quiz_questions[answered_count]
    if quiz_skip:
        # No grading call needed — is_correct=None marks it as skipped
        # rather than wrong, so it doesn't count against the student's
        # score at the end.
        is_correct = None
        feedback = f"Skipped ⏭️ — the answer was *{current_question.correct_answer}*."
    else:
        is_correct, grade_result = await grade_answer(
            current_question.question_text, current_question.correct_answer, message_text
        )
        cost_tracker.record_claude_usage(
            db, grade_result.model, grade_result.input_tokens, grade_result.output_tokens, student.id,
            cache_write_tokens=grade_result.cache_write_tokens, cache_read_tokens=grade_result.cache_read_tokens,
        )
        feedback = "Correct! ✅" if is_correct else f"Not quite — the answer was *{current_question.correct_answer}*."
    db.add(Answer(
        question_id=current_question.id, student_id=student.id,
        given_answer=message_text, is_correct=is_correct,
    ))
    db.commit()
    answered_count += 1

    if answered_count < len(quiz_questions):
        next_question = quiz_questions[answered_count]
        return f"{feedback}\n\nQuestion {answered_count + 1}/{len(quiz_questions)}: {next_question.question_text}"

    all_answers = db.query(Answer).filter(Answer.question_id.in_([q.id for q in quiz_questions])).all()
    score = sum(1 for a in all_answers if a.is_correct is True)
    skipped = sum(1 for a in all_answers if a.is_correct is None)
    attempted = len(quiz_questions) - skipped
    student.active_quiz_id = None
    # Only fetched here (quiz completion), not on every intermediate
    # answer/skip turn — is_mock_test/created_at/title are only needed for
    # the final report.
    current_quiz = db.query(Quiz).filter(Quiz.id == quiz_questions[0].quiz_id).first()

    # A wrong/skipped quiz answer previously was only ever stored in the
    # Answer table for scoring this one quiz — it never fed into
    # weak_topics, so a student who missed something in a quiz never got it
    # proactively revisited in a LATER regular tutoring conversation, only
    # (sometimes) an immediate same-message offer to redo the quiz. Writing
    # a TopicProgress row per wrong/skipped answer here reuses that
    # existing mechanism instead of building a separate one.
    quiz_topic_label = (current_quiz.title if current_quiz else None) or "quiz review"
    questions_by_id = {q.id: q for q in quiz_questions}
    for answer in all_answers:
        if answer.is_correct is False or answer.is_correct is None:
            missed_question = questions_by_id.get(answer.question_id)
            db.add(TopicProgress(
                student_id=student.id, topic=quiz_topic_label,
                question_text=missed_question.question_text if missed_question else None,
                given_answer=answer.given_answer, is_correct=False,
            ))
    db.commit()

    skip_note = f" ({skipped} skipped)" if skipped else ""
    if current_quiz is not None and current_quiz.is_mock_test and current_quiz.created_at:
        started_at = current_quiz.created_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - started_at
        minutes, seconds = divmod(int(elapsed.total_seconds()), 60)
        reply_text = (
            f"{feedback}\n\n🎓 Mock test complete! You scored *{score}/{attempted}*{skip_note} "
            f"in {minutes}m {seconds}s."
        )
    else:
        reply_text = f"{feedback}\n\n🎉 Quiz complete! You scored *{score}/{attempted}*{skip_note}."

    # A real tutor doesn't just report a bad score and move on — offer to
    # actually go back over the material. Only on a genuinely weak showing
    # (not a single skip dragging down an otherwise-fine attempt), and only
    # when there's a topic to offer to re-teach.
    if attempted > 0 and score / attempted < 0.5 and current_quiz is not None and current_quiz.title:
        reply_text += (
            f"\n\nLooks like *{current_quiz.title}* could use more practice — "
            f"want me to go over it again from the start?"
        )
    return reply_text
