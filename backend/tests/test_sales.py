from datetime import datetime, timedelta, timezone

from app.models.core import ChatHistory, Centre, Student
from app.services.sales import CHURN_RISK_INACTIVE_DAYS, get_schools_overview


def _make_centre_with_student(db, sales_status="active", days_since_last_message=None):
    centre = Centre(name="Test School", sales_status=sales_status)
    db.add(centre)
    db.commit()
    student = Student(name="Student", phone="919000000001", centre_id=centre.id)
    db.add(student)
    db.commit()
    if days_since_last_message is not None:
        created_at = datetime.now(timezone.utc) - timedelta(days=days_since_last_message)
        db.add(ChatHistory(student_id=student.id, role="user", message="hi", created_at=created_at))
        db.commit()
    return centre


def test_active_school_with_recent_activity_is_not_churn_risk(pg_db_session):
    _make_centre_with_student(pg_db_session, sales_status="active", days_since_last_message=1)
    overview = get_schools_overview(pg_db_session)
    assert len(overview) == 1
    assert overview[0]["is_churn_risk"] is False


def test_active_school_gone_quiet_is_flagged_as_churn_risk(pg_db_session):
    _make_centre_with_student(
        pg_db_session, sales_status="active", days_since_last_message=CHURN_RISK_INACTIVE_DAYS + 1
    )
    overview = get_schools_overview(pg_db_session)
    assert overview[0]["is_churn_risk"] is True


def test_school_with_no_activity_ever_is_flagged_if_active(pg_db_session):
    _make_centre_with_student(pg_db_session, sales_status="active", days_since_last_message=None)
    overview = get_schools_overview(pg_db_session)
    assert overview[0]["is_churn_risk"] is True
    assert overview[0]["last_activity"] is None


def test_prospect_school_never_flagged_as_churn_risk(pg_db_session):
    """A "prospect" isn't a customer yet — no activity is expected, so no churn signal applies."""
    _make_centre_with_student(pg_db_session, sales_status="prospect", days_since_last_message=None)
    overview = get_schools_overview(pg_db_session)
    assert overview[0]["is_churn_risk"] is False
