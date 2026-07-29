from app.models.core import Centre, Student
from app.services import cost_tracker


def _make_student(db_session, phone="919000000001"):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Test Student", phone=phone, centre_id=centre.id)
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def test_balance_starts_at_zero(db_session):
    student = _make_student(db_session)
    assert cost_tracker.get_balance(db_session, student.id) == 0
    assert cost_tracker.has_credits(db_session, student.id) is False


def test_add_credits_updates_balance(db_session):
    student = _make_student(db_session)
    balance = cost_tracker.add_credits(db_session, student.id, 20.0, note="test")
    assert balance == 20.0
    assert cost_tracker.has_credits(db_session, student.id) is True


def test_trial_credits_match_constant(db_session):
    student = _make_student(db_session)
    balance = cost_tracker.add_trial_credits(db_session, student.id)
    assert balance == cost_tracker.TRIAL_CREDITS


def test_claude_usage_deducts_with_markup(db_session):
    student = _make_student(db_session)
    cost_tracker.add_credits(db_session, student.id, 100.0)
    rates = cost_tracker.PRICING["claude_sonnet"]
    input_tokens, output_tokens = 1000, 500
    expected_raw = (input_tokens / 1000) * rates["input_per_1k_tokens"] + (output_tokens / 1000) * rates["output_per_1k_tokens"]
    balance = cost_tracker.record_claude_usage(db_session, "claude-sonnet-4-6", input_tokens, output_tokens, student.id)
    assert balance == round(100.0 - expected_raw * cost_tracker.MARKUP_MULTIPLIER, 10)


def test_zero_cost_call_does_not_touch_balance(db_session):
    student = _make_student(db_session)
    cost_tracker.add_credits(db_session, student.id, 10.0)
    balance = cost_tracker.record_claude_usage(db_session, "claude-sonnet-4-6", 0, 0, student.id)
    assert balance == 10.0


def test_referral_credit_is_uncapped(db_session):
    """Explicit product decision: referral credits have NO lifetime cap."""
    student = _make_student(db_session)
    for _ in range(10):
        cost_tracker.grant_referral_credit(db_session, student.id, 10.0, note="test referral")
    assert cost_tracker.get_referral_credits_earned(db_session, student.id) == 100.0
    assert cost_tracker.get_balance(db_session, student.id) == 100.0


def test_habit_credit_grant(db_session):
    student = _make_student(db_session)
    balance = cost_tracker.grant_habit_credit(db_session, student.id, 5.0, note="Habit milestone: day1")
    assert balance == 5.0
    assert cost_tracker.get_balance(db_session, student.id) == 5.0


def test_payment_idempotency_guard(db_session):
    """The fix for the double-crediting bug found in the audit."""
    student = _make_student(db_session)
    assert cost_tracker.has_processed_external_ref(db_session, "pay_abc123") is False

    cost_tracker.add_credits(db_session, student.id, 100.0, note="payment", external_ref="pay_abc123")
    assert cost_tracker.has_processed_external_ref(db_session, "pay_abc123") is True
    assert cost_tracker.get_balance(db_session, student.id) == 100.0

    # A second /pay/verify call for the SAME payment must be recognized as
    # already-processed by the caller before ever calling add_credits again.
    if not cost_tracker.has_processed_external_ref(db_session, "pay_abc123"):
        cost_tracker.add_credits(db_session, student.id, 100.0, note="payment retry", external_ref="pay_abc123")
    assert cost_tracker.get_balance(db_session, student.id) == 100.0  # unchanged, not 200


def test_duplicate_external_ref_rejected_at_db_level(db_session):
    """Defense in depth — even bypassing the app-level check, the unique
    constraint on external_ref must prevent a literal duplicate row."""
    import pytest
    from sqlalchemy.exc import IntegrityError
    from app.models.core import CreditEvent

    student = _make_student(db_session)
    db_session.add(CreditEvent(amount=50, student_id=student.id, external_ref="pay_unique"))
    db_session.commit()

    db_session.add(CreditEvent(amount=50, student_id=student.id, external_ref="pay_unique"))
    with pytest.raises(IntegrityError):
        db_session.commit()
