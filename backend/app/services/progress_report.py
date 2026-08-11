from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import Answer, ChatHistory, Chapter, Question, Quiz, Subject, TopicProgress
from app.services.text_utils import tokenize_words

# A student's free-text `board` value maps to the `Subject.board` the
# seeded curriculum is actually tagged with (see scripts/seed_ncert_
# curriculum.py and scripts/seed_bseb_curriculum.py) — chapter names/order
# don't reliably carry over between boards, which use different textbooks,
# so coverage must only ever match against the SAME board's seeded data.
# A student with no board on file yet defaults to CBSE (most likely in
# this market), never silently matched against every board's chapters.
_BOARD_TO_SUBJECT_BOARD = {"": "CBSE", "cbse": "CBSE", "ncert": "CBSE", "bseb": "BSEB"}
_STOPWORDS = {"the", "and", "of", "in", "on", "a", "an", "to", "for", "with"}


def get_student_stats(db: Session, student_id: int, days: int | None = None) -> dict:
    """
    Aggregate TopicProgress + message-volume stats for a student, optionally
    scoped to the last `days` days (for a weekly digest) or all-time (for
    the student's own "how am I doing?" summary).
    """
    query = db.query(TopicProgress).filter(TopicProgress.student_id == student_id)
    if days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.filter(TopicProgress.created_at >= since)
    rows = query.all()

    topic_counts: dict[str, dict[str, int]] = {}
    correct = incorrect = 0
    for row in rows:
        counts = topic_counts.setdefault(row.topic, {"correct": 0, "incorrect": 0})
        if row.is_correct is True:
            correct += 1
            counts["correct"] += 1
        elif row.is_correct is False:
            incorrect += 1
            counts["incorrect"] += 1

    # Quiz/mock-test answers are tracked in a completely separate table
    # (Answer, via Question/Quiz) from the inline tutoring check-questions
    # above (TopicProgress) — confirmed live a student could correctly
    # answer a real scored quiz question and "my progress" would show zero
    # reflection of it, since this method never looked at the quiz tables
    # at all. Answer has no created_at of its own, so a `days`-scoped query
    # approximates using the parent Quiz's created_at instead — a quiz is
    # answered over minutes, not days, so this is a close enough proxy.
    # Kept out of topic_counts/weak_topics (below) since a quiz answer has
    # no reliable topic string of its own to attribute it to, only merged
    # into the overall correct/incorrect/total counts.
    answer_query = (
        db.query(Answer.is_correct)
        .join(Question, Question.id == Answer.question_id)
        .join(Quiz, Quiz.id == Question.quiz_id)
        .filter(Answer.student_id == student_id)
    )
    if days is not None:
        answer_query = answer_query.filter(Quiz.created_at >= since)
    for (is_correct,) in answer_query.all():
        if is_correct is True:
            correct += 1
        elif is_correct is False:
            incorrect += 1

    total = correct + incorrect
    # A topic counts as "weak" if wrong answers there outnumber right ones —
    # a simple, explainable rule rather than a stricter statistical test,
    # since these are small sample counts per topic.
    weak_topics = [topic for topic, c in topic_counts.items() if c["incorrect"] > c["correct"]]

    message_query = db.query(func.count(ChatHistory.id)).filter(
        ChatHistory.student_id == student_id, ChatHistory.role == "user"
    )
    if days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        message_query = message_query.filter(ChatHistory.created_at >= since)
    message_count = message_query.scalar()

    return {
        "total_evaluated": total,
        "correct": correct,
        "incorrect": incorrect,
        "accuracy_pct": round(correct / total * 100) if total else None,
        "weak_topics": weak_topics,
        "topic_counts": topic_counts,
        "messages_sent": message_count,
    }


def get_activity_stats(db: Session, student_id: int) -> dict:
    """
    Distinct active days and current consecutive-day streak, computed from
    chat_history — used both to show a streak in the progress summary and
    to decide whether a "welcome back" note is warranted.
    """
    rows = (
        db.query(func.date(ChatHistory.created_at))
        .filter(ChatHistory.student_id == student_id, ChatHistory.role == "user")
        .distinct()
        .all()
    )
    dates = sorted({r[0] for r in rows}, reverse=True)
    if not dates:
        return {"active_days": 0, "streak_days": 0, "days_since_last_message": None}

    today = datetime.now(timezone.utc).date()
    days_since_last_message = (today - dates[0]).days

    streak = 0
    if dates[0] in (today, today - timedelta(days=1)):
        streak = 1
        for i in range(1, len(dates)):
            if (dates[i - 1] - dates[i]).days == 1:
                streak += 1
            else:
                break

    return {"active_days": len(dates), "streak_days": streak, "days_since_last_message": days_since_last_message}


def get_welcome_back_note(db: Session, student_id: int, gap_threshold_days: int = 3) -> str | None:
    """
    A brief re-engagement note when a student returns after a gap — without
    this, HISTORY_TURNS being a sliding window means a returning student
    gets a cold-start conversation with no acknowledgment of the gap at all.
    Returns None if the student is new, or was active recently enough that
    no note is needed.
    """
    activity = get_activity_stats(db, student_id)
    gap = activity["days_since_last_message"]
    if gap is None or gap < gap_threshold_days:
        return None

    last_topic_row = (
        db.query(TopicProgress.topic)
        .filter(TopicProgress.student_id == student_id)
        .order_by(TopicProgress.created_at.desc())
        .first()
    )
    if last_topic_row:
        return f"Welcome back! 👋 It's been {gap} days — last time we were working on *{last_topic_row[0]}*."
    return f"Welcome back! 👋 It's been {gap} days since we last talked."


def _significant_words(text: str) -> set[str]:
    return {w for w in tokenize_words(text) if len(w) >= 4 and w not in _STOPWORDS}


def get_chapter_coverage(db: Session, student) -> dict | None:
    """
    Which seeded chapters (for this student's class AND board) have been
    touched based on topics discussed, vs. not yet — a fuzzy word-overlap
    match against free-text TopicProgress.topic values, since topics aren't
    linked to a specific chapter_id anywhere in the tutoring flow yet.
    Returns None when not applicable: no class on file, or a board with no
    seeded curriculum at all (see _BOARD_TO_SUBJECT_BOARD).
    """
    board = (student.board or "").strip().lower()
    subject_board = _BOARD_TO_SUBJECT_BOARD.get(board)
    if subject_board is None or not student.class_:
        return None

    chapters = (
        db.query(Chapter)
        .join(Subject, Chapter.subject_id == Subject.id)
        .filter(Subject.class_ == student.class_, Subject.board == subject_board)
        .all()
    )
    if not chapters:
        return None

    topic_rows = db.query(TopicProgress.topic).filter(TopicProgress.student_id == student.id).distinct().all()
    topic_word_sets = [_significant_words(t[0]) for t in topic_rows if t[0]]

    covered, not_covered = [], []
    for chapter in chapters:
        chapter_words = _significant_words(chapter.name)
        touched = any(topic_words & chapter_words for topic_words in topic_word_sets)
        (covered if touched else not_covered).append(chapter.name)

    return {"covered": covered, "not_covered": not_covered, "total": len(chapters)}


def format_progress_message(stats: dict, activity: dict | None = None, coverage: dict | None = None) -> str:
    if stats["total_evaluated"] == 0:
        return (
            "You haven't answered any check questions yet — keep chatting with me and I'll start "
            "tracking your progress! 📊"
        )
    lines = [
        "📊 Your progress so far:",
        f"- {stats['total_evaluated']} questions checked, {stats['correct']} correct ({stats['accuracy_pct']}%)",
    ]
    if stats["weak_topics"]:
        lines.append(f"- Topics to review: {', '.join(stats['weak_topics'])}")
    else:
        lines.append("- No major weak spots right now — nice work! 👏")
    if activity and activity["streak_days"] >= 2:
        lines.append(f"- 🔥 {activity['streak_days']}-day streak — keep it going!")
    if coverage and coverage["total"] > 0:
        # Not "NCERT chapters" — get_chapter_coverage also serves BSEB
        # students now (see _BOARD_TO_SUBJECT_BOARD), and naming the wrong
        # board here would be a real, visible inaccuracy in their own
        # progress report.
        lines.append(f"- 📚 Covered {len(coverage['covered'])}/{coverage['total']} chapters for your class this year")
        if coverage["not_covered"]:
            preview = ", ".join(coverage["not_covered"][:3])
            lines.append(f"  Not touched yet: {preview}{'...' if len(coverage['not_covered']) > 3 else ''}")
    lines.append("\nKeep it up — the more we practice together, the better this gets!")
    return "\n".join(lines)


def format_parent_digest(student_name: str, stats: dict, activity: dict) -> str:
    """
    Weekly digest sent directly to a parent's own WhatsApp (see
    scripts/send_parent_digests.py) — warmer, "your child" framing, and
    deliberately omits the hint-vs-direct-solve academic-integrity signal
    from format_teacher_digest, which is meaningful to a teacher but not to
    a parent unfamiliar with the underlying pedagogy.
    """
    if stats["messages_sent"] == 0:
        return (
            f"Hi! This week, {student_name} didn't chat with their Qlass AI tutor at all. "
            f"A gentle nudge to check in with them might help. 📚"
        )
    lines = [f"📊 {student_name}'s week with the Qlass AI Tutor:", f"- {stats['messages_sent']} messages exchanged"]
    if stats["total_evaluated"] > 0:
        lines.append(f"- {stats['correct']}/{stats['total_evaluated']} check questions correct ({stats['accuracy_pct']}%)")
    if stats["weak_topics"]:
        lines.append(f"- Could use more practice on: {', '.join(stats['weak_topics'])}")
    if activity["streak_days"] >= 2:
        lines.append(f"- 🔥 {activity['streak_days']}-day streak — keep encouraging them!")
    lines.append("\nThe more they chat with their AI tutor, the more this can help them.")
    return "\n".join(lines)


def format_teacher_digest(student_name: str, stats: dict, hints_given: int = 0, direct_solutions: int = 0) -> str:
    if stats["messages_sent"] == 0:
        return f"*{student_name}* — no activity this week."
    lines = [f"*{student_name}* — this week:", f"- {stats['messages_sent']} messages sent"]
    if stats["total_evaluated"] > 0:
        lines.append(f"- {stats['correct']}/{stats['total_evaluated']} check questions correct ({stats['accuracy_pct']}%)")
    if stats["weak_topics"]:
        lines.append(f"- Weak topics: {', '.join(stats['weak_topics'])}")
    # Academic-integrity signal: all-time counts (these aren't reset weekly),
    # so a teacher can see the overall balance of "worked it out themselves"
    # vs "asked for the direct answer" for this student.
    total_problems = hints_given + direct_solutions
    if total_problems > 0:
        lines.append(f"- Problem-solving: {hints_given} hinted, {direct_solutions} solved directly (all-time)")
    return "\n".join(lines)
