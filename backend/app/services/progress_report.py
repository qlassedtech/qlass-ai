import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import ChatHistory, TopicProgress, Subject, Chapter

# The seeded curriculum (see scripts/seed_ncert_curriculum.py) is the NCERT
# syllabus specifically. NCERT chapter names/order don't reliably apply to
# ICSE or most State boards, which use entirely different textbooks — only
# match coverage against it when the student is CBSE or hasn't told us
# their board yet (most likely CBSE/NCERT-aligned by default in this
# market), never for a board we know is different.
_NCERT_ALIGNED_BOARDS = {"", "cbse", "ncert"}
_STOPWORDS = {"the", "and", "of", "in", "on", "a", "an", "to", "for", "with"}

_PROGRESS_REQUEST_PHRASES = [
    "how am i doing", "how am i doin", "my progress", "mera progress", "meri progress",
    "progress report", "show my progress", "how is my progress", "kaisa kar raha",
    "kaisi kar rahi", "kitna sahi", "score kya hai", "my score", "mera score",
]


def looks_like_progress_request(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _PROGRESS_REQUEST_PHRASES)


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
    return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) >= 4 and w not in _STOPWORDS}


def get_chapter_coverage(db: Session, student) -> dict | None:
    """
    Which seeded NCERT chapters (for this student's class) have been
    touched based on topics discussed, vs. not yet — a fuzzy word-overlap
    match against free-text TopicProgress.topic values, since topics aren't
    linked to a specific chapter_id anywhere in the tutoring flow yet.
    Returns None when not applicable: no class on file, or a board other
    than CBSE/NCERT/unset (see _NCERT_ALIGNED_BOARDS).
    """
    board = (student.board or "").strip().lower()
    if board not in _NCERT_ALIGNED_BOARDS or not student.class_:
        return None

    chapters = (
        db.query(Chapter)
        .join(Subject, Chapter.subject_id == Subject.id)
        .filter(Subject.class_ == student.class_)
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
        lines.append(f"- 📚 Covered {len(coverage['covered'])}/{coverage['total']} NCERT chapters for your class this year")
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
