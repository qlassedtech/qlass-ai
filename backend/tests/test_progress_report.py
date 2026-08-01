from datetime import datetime, timedelta, timezone

from app.models.core import Chapter, ChatHistory, Student, Subject, TopicProgress
from app.services.progress_report import (
    format_progress_message,
    get_chapter_coverage,
    get_student_stats,
)


def _make_student(db_session, board="CBSE", class_="10"):
    student = Student(name="Test Student", phone="9990001111", class_=class_, board=board)
    db_session.add(student)
    db_session.commit()
    return student


def test_get_student_stats_counts_correct_and_incorrect(db_session):
    student = _make_student(db_session)
    db_session.add_all(
        [
            TopicProgress(student_id=student.id, topic="Fractions", is_correct=True),
            TopicProgress(student_id=student.id, topic="Fractions", is_correct=False),
            TopicProgress(student_id=student.id, topic="Fractions", is_correct=False),
        ]
    )
    db_session.commit()

    stats = get_student_stats(db_session, student.id)

    assert stats["total_evaluated"] == 3
    assert stats["correct"] == 1
    assert stats["incorrect"] == 2
    assert stats["weak_topics"] == ["Fractions"]


def test_get_student_stats_no_data_yet(db_session):
    student = _make_student(db_session)
    stats = get_student_stats(db_session, student.id)
    assert stats["total_evaluated"] == 0
    assert stats["accuracy_pct"] is None
    assert stats["weak_topics"] == []


def test_chapter_coverage_matches_only_same_board(db_session):
    # Same class+name subjects under two different boards must never mix —
    # this is exactly the bug class _BOARD_TO_SUBJECT_BOARD guards against.
    cbse_subject = Subject(name="Mathematics", class_="10", board="CBSE")
    bseb_subject = Subject(name="Mathematics", class_="10", board="BSEB")
    db_session.add_all([cbse_subject, bseb_subject])
    db_session.commit()

    db_session.add_all(
        [
            Chapter(subject_id=cbse_subject.id, name="Real Numbers"),
            Chapter(subject_id=bseb_subject.id, name="Trigonometry"),
        ]
    )
    db_session.commit()

    cbse_student = _make_student(db_session, board="CBSE")
    coverage = get_chapter_coverage(db_session, cbse_student)

    assert coverage["total"] == 1
    assert coverage["not_covered"] == ["Real Numbers"]


def test_chapter_coverage_marks_topic_as_covered_on_word_overlap(db_session):
    subject = Subject(name="Mathematics", class_="10", board="CBSE")
    db_session.add(subject)
    db_session.commit()
    chapter = Chapter(subject_id=subject.id, name="Real Numbers")
    db_session.add(chapter)
    db_session.commit()

    student = _make_student(db_session, board="CBSE")
    db_session.add(TopicProgress(student_id=student.id, topic="Real Numbers basics"))
    db_session.commit()

    coverage = get_chapter_coverage(db_session, student)

    assert coverage["covered"] == ["Real Numbers"]
    assert coverage["not_covered"] == []


def test_chapter_coverage_none_when_no_class_on_file(db_session):
    student = _make_student(db_session, class_=None)
    assert get_chapter_coverage(db_session, student) is None


def test_chapter_coverage_none_for_unseeded_board(db_session):
    student = _make_student(db_session, board="ICSE")
    assert get_chapter_coverage(db_session, student) is None


def test_format_progress_message_omits_board_name(db_session):
    # Regression test: format_progress_message previously hardcoded
    # "NCERT chapters" even for BSEB students whose coverage came from
    # get_chapter_coverage's BSEB-scoped chapters — a real, visible
    # inaccuracy in their own progress report.
    stats = {"total_evaluated": 5, "correct": 4, "incorrect": 1, "accuracy_pct": 80, "weak_topics": []}
    coverage = {"covered": ["Chapter A"], "not_covered": ["Chapter B"], "total": 2}

    message = format_progress_message(stats, activity=None, coverage=coverage)

    assert "NCERT" not in message
    assert "2/2" not in message
    assert "Covered 1/2 chapters" in message


def test_format_progress_message_no_data_yet():
    stats = {"total_evaluated": 0}
    message = format_progress_message(stats)
    assert "haven't answered" in message
