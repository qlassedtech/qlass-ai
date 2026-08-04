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


def test_unlimited_plan_bypasses_empty_wallet(db_session):
    from datetime import datetime, timedelta, timezone

    student = _make_student(db_session)
    assert cost_tracker.has_credits(db_session, student.id) is False  # empty wallet, normal plan

    student.subscription_plan = "unlimited"
    student.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    db_session.commit()
    assert cost_tracker.has_credits(db_session, student.id) is True


def test_expired_unlimited_plan_falls_back_to_wallet(db_session):
    from datetime import datetime, timedelta, timezone

    student = _make_student(db_session)
    student.subscription_plan = "unlimited"
    student.subscription_expires_at = datetime.now(timezone.utc) - timedelta(days=1)  # expired yesterday
    db_session.commit()
    assert cost_tracker.has_credits(db_session, student.id) is False


def test_unlimited_plan_usage_does_not_deduct_wallet(db_session):
    from datetime import datetime, timedelta, timezone

    student = _make_student(db_session)
    student.subscription_plan = "unlimited"
    student.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    db_session.commit()

    balance = cost_tracker.record_claude_usage(db_session, "claude-sonnet-4-6", 1000, 500, student.id)
    assert balance == 0.0  # wallet untouched, but a $0 CreditEvent is still logged with real raw_cost

    from app.models.core import CreditEvent
    event = db_session.query(CreditEvent).filter(CreditEvent.student_id == student.id).first()
    assert event.amount == 0
    assert event.raw_cost > 0


def test_unlimited_plan_blocks_once_period_cap_hit_with_empty_wallet(db_session):
    """
    The bug this replaces: unlimited used to mean literally uncapped, so a
    single heavy student could erase the flat fee's own margin. Now hitting
    the weekly/monthly allotment (see business_rules.UNLIMITED_PERIOD_SPEND_CAPS)
    blocks further *free* usage — but only once their wallet is also empty,
    since paying for "usage credits" should keep them going (see the next
    test), not hit a dead end.
    """
    from datetime import datetime, timedelta, timezone

    student = _make_student(db_session)
    student.subscription_plan = "unlimited"
    student.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    db_session.commit()

    weekly_cap = cost_tracker.UNLIMITED_PERIOD_SPEND_CAPS["student"]["week"]
    # Burn through the weekly allotment with real (billed-equivalent) cost.
    raw_cost_needed = weekly_cap / cost_tracker.MARKUP_MULTIPLIER
    cost_tracker._deduct(db_session, "claude_sonnet", raw_cost_needed, student.id)

    assert cost_tracker.is_unlimited_over_period_cap(db_session, student) is True
    assert cost_tracker.has_credits(db_session, student.id) is False  # over cap, no wallet balance


def test_unlimited_plan_over_cap_draws_from_topped_up_wallet(db_session):
    """
    "Usage credits": once over the period cap, a student who tops up keeps
    going on their own wallet — the SAME wallet a pay-as-you-go student
    uses — rather than being stuck until the period resets.
    """
    from datetime import datetime, timedelta, timezone

    student = _make_student(db_session)
    student.subscription_plan = "unlimited"
    student.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    db_session.commit()

    weekly_cap = cost_tracker.UNLIMITED_PERIOD_SPEND_CAPS["student"]["week"]
    raw_cost_needed = weekly_cap / cost_tracker.MARKUP_MULTIPLIER
    cost_tracker._deduct(db_session, "claude_sonnet", raw_cost_needed, student.id)
    assert cost_tracker.has_credits(db_session, student.id) is False

    cost_tracker.add_credits(db_session, student.id, 50.0, note="usage credits top-up")
    assert cost_tracker.has_credits(db_session, student.id) is True

    # Further usage now actually draws down the wallet (no longer amount=0).
    balance_before = cost_tracker.get_balance(db_session, student.id)
    balance_after = cost_tracker.record_claude_usage(db_session, "claude-sonnet-4-6", 1000, 500, student.id)
    assert balance_after < balance_before


def test_unlimited_plan_under_cap_still_free(db_session):
    from datetime import datetime, timedelta, timezone

    student = _make_student(db_session)
    student.subscription_plan = "unlimited"
    student.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    db_session.commit()

    assert cost_tracker.is_unlimited_over_period_cap(db_session, student) is False
    assert cost_tracker.has_credits(db_session, student.id) is True
    balance = cost_tracker.record_claude_usage(db_session, "claude-sonnet-4-6", 1000, 500, student.id)
    assert balance == 0.0  # still covered by the flat fee, wallet untouched


def test_get_last_recharge_skips_referral_and_habit_bonuses(db_session):
    student = _make_student(db_session)
    cost_tracker.add_trial_credits(db_session, student.id)
    cost_tracker.grant_referral_credit(db_session, student.id, 10.0, note="referral")
    cost_tracker.grant_habit_credit(db_session, student.id, 5.0, note="habit")

    recharge = cost_tracker.get_last_recharge(db_session, student.id)
    assert recharge is not None
    assert recharge.amount == cost_tracker.TRIAL_CREDITS  # the trial grant, not the later bonuses


def test_get_usage_fraction_for_wallet_student_tracks_last_recharge(db_session):
    import pytest
    from datetime import datetime, timedelta, timezone
    from app.models.core import CreditEvent

    # Explicit, well-separated timestamps rather than two real-time writes —
    # SQLite's CURRENT_TIMESTAMP has only whole-second resolution, so two
    # events written in the same wall-clock second can otherwise land on an
    # ambiguous ">=" boundary that Postgres (real microsecond precision)
    # would never hit.
    student = _make_student(db_session)
    recharge_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.add(CreditEvent(amount=100.0, student_id=student.id, note="top-up", created_at=recharge_time))
    db_session.commit()

    spend_time = recharge_time + timedelta(minutes=1)
    db_session.add(CreditEvent(
        amount=-25.0, raw_cost=12.5, service="claude_sonnet", student_id=student.id, created_at=spend_time,
    ))
    db_session.commit()

    fraction = cost_tracker.get_usage_fraction(db_session, student)
    assert fraction == pytest.approx(0.25)


def test_usage_threshold_notice_fires_once_when_crossing():
    assert cost_tracker.usage_threshold_notice(0.4, 0.6) is not None  # crossed 50%
    assert cost_tracker.usage_threshold_notice(0.6, 0.6) is None  # no movement, no re-notify
    assert cost_tracker.usage_threshold_notice(0.95, 1.0) is None  # 100% has its own dedicated notice, not this one
    assert cost_tracker.usage_threshold_notice(None, 0.5) is not None


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
