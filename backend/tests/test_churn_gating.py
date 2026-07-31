from app.models.core import Centre, CreditEvent, Student
from app.services.cost_tracker import has_independent_payment
from app.services.school_billing import is_centre_churned


def _make_student(db_session, sales_status="active"):
    centre = Centre(name="Test School", sales_status=sales_status)
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Test Student", phone="919000000001", centre_id=centre.id)
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def test_active_school_is_not_churned(db_session):
    student = _make_student(db_session, sales_status="active")
    assert is_centre_churned(db_session, student.centre_id) is False


def test_churned_school_is_churned(db_session):
    student = _make_student(db_session, sales_status="churned")
    assert is_centre_churned(db_session, student.centre_id) is True


def test_no_centre_is_never_churned(db_session):
    assert is_centre_churned(db_session, None) is False


def test_trial_and_bonus_credits_do_not_count_as_independent_payment(db_session):
    student = _make_student(db_session)
    db_session.add(CreditEvent(amount=50.0, student_id=student.id, note="Qlass trial credit"))
    db_session.add(CreditEvent(amount=10.0, student_id=student.id, service="referral_bonus"))
    db_session.commit()
    assert has_independent_payment(db_session, student.id) is False


def test_real_razorpay_payment_counts_as_independent_payment(db_session):
    student = _make_student(db_session)
    db_session.add(CreditEvent(amount=100.0, student_id=student.id, external_ref="pay_abc123"))
    db_session.commit()
    assert has_independent_payment(db_session, student.id) is True
