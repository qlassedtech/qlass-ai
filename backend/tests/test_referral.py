from datetime import datetime, timedelta

from app.models.core import ChatHistory, Centre, Student
from app.services import cost_tracker
from app.services.referral import (
    extract_referral_code,
    generate_referral_code,
    evaluate_referral_milestones,
    REFERRAL_MILESTONES,
)

# Uses the pg_db_session fixture (real Postgres, transaction rolled back
# after each test) rather than the fast in-memory SQLite one — SQLite
# silently drops tzinfo on round-trip, which breaks the aware-vs-naive
# datetime comparison inside evaluate_referral_milestones even though that
# logic is correct against real Postgres (verified live during development).
def _utcnow():
    return datetime.utcnow()


def _make_pair(pg_db_session):
    centre = Centre(name="Test School")
    pg_db_session.add(centre)
    pg_db_session.commit()
    referrer = Student(name="Referrer", phone="919000000001", centre_id=centre.id)
    pg_db_session.add(referrer)
    pg_db_session.commit()
    pg_db_session.refresh(referrer)
    referrer.referral_code = generate_referral_code(referrer.id)
    pg_db_session.commit()

    referred = Student(
        name="Referred", phone="919000000002", centre_id=centre.id, referred_by_id=referrer.id,
    )
    pg_db_session.add(referred)
    pg_db_session.commit()
    pg_db_session.refresh(referred)
    return referrer, referred


def test_generate_and_extract_referral_code_roundtrip():
    code = generate_referral_code(42)
    assert extract_referral_code(f"hi my code is {code}") == code
    assert extract_referral_code("no code mentioned here") is None


def _add_question(pg_db_session, student_id, at: datetime):
    pg_db_session.add(ChatHistory(student_id=student_id, role="user", message="q", agent="tutor", created_at=at))
    pg_db_session.commit()


async def test_day1_milestone_requires_two_questions(pg_db_session):
    referrer, referred = _make_pair(pg_db_session)
    signup = _utcnow() - timedelta(days=1, hours=12)
    referred.created_at = signup
    pg_db_session.commit()

    # only one question in the day1 window — should NOT pay yet (day1 needs 2)
    _add_question(pg_db_session, referred.id, signup + timedelta(days=1, hours=2))
    await evaluate_referral_milestones(pg_db_session, referred)
    assert "day1" not in (referred.referral_milestones_paid or [])
    balance_before = cost_tracker.get_balance(pg_db_session, referrer.id)

    # a second question in the same window — should pay now
    _add_question(pg_db_session, referred.id, signup + timedelta(days=1, hours=3))
    await evaluate_referral_milestones(pg_db_session, referred)
    pg_db_session.refresh(referred)
    assert "day1" in referred.referral_milestones_paid
    assert cost_tracker.get_balance(pg_db_session, referrer.id) == balance_before + 10.0


async def test_referral_milestones_never_repay_same_milestone(pg_db_session):
    referrer, referred = _make_pair(pg_db_session)
    signup = _utcnow() - timedelta(days=1, hours=12)
    referred.created_at = signup
    pg_db_session.commit()
    _add_question(pg_db_session, referred.id, signup + timedelta(days=1, hours=2))
    _add_question(pg_db_session, referred.id, signup + timedelta(days=1, hours=3))

    await evaluate_referral_milestones(pg_db_session, referred)
    await evaluate_referral_milestones(pg_db_session, referred)  # simulate a second message in the same window
    pg_db_session.refresh(referred)

    assert referred.referral_milestones_paid.count("day1") == 1
    assert cost_tracker.get_referral_credits_earned(pg_db_session, referrer.id) == 10.0


async def test_referral_milestones_are_uncapped(pg_db_session):
    """Explicit product decision: no lifetime cap on referral earnings."""
    referrer, referred = _make_pair(pg_db_session)

    for name, start, end, min_questions, _bonus in REFERRAL_MILESTONES:
        mid_days = (start + end) / 2
        referred.created_at = _utcnow() - timedelta(days=mid_days)
        pg_db_session.commit()
        for i in range(min_questions):
            _add_question(pg_db_session, referred.id, referred.created_at + timedelta(days=mid_days - 0.01, hours=i))
        await evaluate_referral_milestones(pg_db_session, referred)

    total_bonus = sum(b for _, _, _, _, b in REFERRAL_MILESTONES)
    assert cost_tracker.get_referral_credits_earned(pg_db_session, referrer.id) == total_bonus
    assert total_bonus > 50.0  # would have exceeded the old (now-removed) 50-credit cap
