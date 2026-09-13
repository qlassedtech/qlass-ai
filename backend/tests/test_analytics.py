import pytest

from app.models.core import Centre, CreditEvent, Student, TopicProgress
from app.services.analytics import MIN_EVALUATED_FOR_RISK, get_ai_cost_breakdown, get_school_analytics
from app.services.escalation import ESCALATION_THRESHOLD
from app.services import cost_tracker, school_billing


def _make_student(db_session, name="Student", consecutive_unresolved_hints=0):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(
        name=name, phone="919000000001", centre_id=centre.id,
        consecutive_unresolved_hints=consecutive_unresolved_hints,
    )
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def test_poor_accuracy_with_enough_samples_flags_as_at_risk(db_session):
    student = _make_student(db_session)
    for i in range(MIN_EVALUATED_FOR_RISK):
        # 1 correct out of MIN_EVALUATED_FOR_RISK -> well under the 50% threshold
        is_correct = i == 0
        db_session.add(TopicProgress(student_id=student.id, topic="algebra", is_correct=is_correct))
    db_session.commit()

    result = get_school_analytics(db_session, [student.id], centre_id=None)
    at_risk_ids = {s["id"] for s in result["at_risk_students"]}
    assert student.id in at_risk_ids


def test_poor_accuracy_with_too_few_samples_is_not_flagged(db_session):
    student = _make_student(db_session)
    db_session.add(TopicProgress(student_id=student.id, topic="algebra", is_correct=False))
    db_session.commit()

    result = get_school_analytics(db_session, [student.id], centre_id=None)
    assert result["at_risk_students"] == []


def test_good_accuracy_is_not_flagged(db_session):
    student = _make_student(db_session)
    for _ in range(MIN_EVALUATED_FOR_RISK):
        db_session.add(TopicProgress(student_id=student.id, topic="algebra", is_correct=True))
    db_session.commit()

    result = get_school_analytics(db_session, [student.id], centre_id=None)
    assert result["at_risk_students"] == []


def test_high_hint_streak_flags_as_at_risk_even_with_no_topic_progress(db_session):
    student = _make_student(db_session, consecutive_unresolved_hints=ESCALATION_THRESHOLD - 1)
    result = get_school_analytics(db_session, [student.id], centre_id=None)
    at_risk_ids = {s["id"] for s in result["at_risk_students"]}
    assert student.id in at_risk_ids


def _make_centre(db_session, name="Test School"):
    centre = Centre(name=name)
    db_session.add(centre)
    db_session.commit()
    db_session.refresh(centre)
    return centre


def test_ai_cost_breakdown_groups_by_feature_and_tier(db_session):
    """
    The core promise of get_ai_cost_breakdown: model tier alone
    (claude_sonnet/claude_haiku) previously couldn't say which FEATURE
    drove spend — this sums raw_cost and counts calls per
    (feature, tier) combination.
    """
    student = _make_student(db_session)
    cost_tracker.record_claude_usage(
        db_session, "claude-sonnet-4-6", 1000, 500, student.id, feature="tutor_reply",
    )
    cost_tracker.record_claude_usage(
        db_session, "claude-sonnet-4-6", 2000, 1000, student.id, feature="tutor_reply",
    )
    cost_tracker.record_claude_usage(
        db_session, "claude-haiku-4-5-20251001", 500, 200, student.id, feature="quiz_generate",
    )

    breakdown = get_ai_cost_breakdown(db_session, days=30)
    by_key = {(row["feature"], row["tier"]): row for row in breakdown}

    assert ("tutor_reply", "claude_sonnet") in by_key
    tutor_row = by_key[("tutor_reply", "claude_sonnet")]
    assert tutor_row["call_count"] == 2
    rates = cost_tracker.PRICING["claude_sonnet"]
    expected_cost = (
        (1000 / 1000) * rates["input_per_1k_tokens"] + (500 / 1000) * rates["output_per_1k_tokens"]
        + (2000 / 1000) * rates["input_per_1k_tokens"] + (1000 / 1000) * rates["output_per_1k_tokens"]
    )
    assert tutor_row["total_cost"] == pytest.approx(expected_cost)

    assert ("quiz_generate", "claude_haiku") in by_key
    assert by_key[("quiz_generate", "claude_haiku")]["call_count"] == 1

    # Sorted with the biggest spend first.
    assert breakdown[0]["total_cost"] >= breakdown[-1]["total_cost"]


def test_ai_cost_breakdown_unlabeled_rows_still_counted(db_session):
    """Pre-existing rows with no feature label must not silently disappear from the total."""
    student = _make_student(db_session)
    cost_tracker.record_claude_usage(db_session, "claude-sonnet-4-6", 1000, 500, student.id)  # no feature=

    breakdown = get_ai_cost_breakdown(db_session, days=30)
    unlabeled = [row for row in breakdown if row["feature"] == "unlabeled"]
    assert len(unlabeled) == 1
    assert unlabeled[0]["call_count"] == 1


def test_ai_cost_breakdown_includes_school_ledger_features(db_session):
    """
    Workbook/roster-extraction/quiz-assignment generation bills the SCHOOL
    ledger (SchoolCreditEvent), not the per-student one — the breakdown
    must include both, or a school-billed feature like "workbook" would be
    invisible next to a student-billed one like "tutor_reply".
    """
    centre = _make_centre(db_session)
    school_billing.record_claude_usage(
        db_session, centre.id, "workbook_pdf", 3000, 1500, feature="workbook",
    )

    breakdown = get_ai_cost_breakdown(db_session, days=30)
    by_key = {(row["feature"], row["tier"]): row for row in breakdown}
    assert ("workbook", "workbook_pdf") in by_key
    assert by_key[("workbook", "workbook_pdf")]["call_count"] == 1


def test_ai_cost_breakdown_respects_date_window(db_session):
    from datetime import datetime, timedelta, timezone

    student = _make_student(db_session)
    old_event = CreditEvent(
        amount=-10.0, service="claude_sonnet", raw_cost=5.0, student_id=student.id, feature="tutor_reply",
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    db_session.add(old_event)
    db_session.commit()

    breakdown = get_ai_cost_breakdown(db_session, days=30)
    assert breakdown == []  # outside the 30-day window

    breakdown_wide = get_ai_cost_breakdown(db_session, days=90)
    assert len(breakdown_wide) == 1
