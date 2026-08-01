from datetime import datetime, timezone

import pytest

from app.models.core import Centre, SchoolPilotGrant, Student
from app.services import cost_tracker, school_billing
from app.services.school_pilot import PILOT_CREDIT_SERVICE, PILOT_STUDENT_FEATURES, launch_pilot


def test_launch_pilot_marks_school_trial_and_funds_selected_students(db_session):
    centre = Centre(name="Pilot School")
    db_session.add(centre)
    db_session.commit()
    selected = Student(name="Selected", phone="919000000001", centre_id=centre.id)
    unselected = Student(name="Unselected", phone="919000000002", centre_id=centre.id)
    db_session.add_all([selected, unselected])
    db_session.commit()

    students = launch_pilot(db_session, centre, [selected.id], 40.0, 30)

    db_session.refresh(centre)
    assert [student.id for student in students] == [selected.id]
    assert centre.sales_status == "trial"
    assert centre.pilot_status == "active"
    assert centre.pilot_expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    assert cost_tracker.get_balance(db_session, selected.id) == 40.0
    assert cost_tracker.get_balance(db_session, unselected.id) == 0.0
    grant = db_session.query(SchoolPilotGrant).filter(SchoolPilotGrant.student_id == selected.id).one()
    assert float(grant.amount) == 40.0
    assert db_session.query(SchoolPilotGrant).count() == 1
    assert cost_tracker.get_usage_count(db_session, selected.id, [PILOT_CREDIT_SERVICE], "month") == 1
    assert school_billing.get_balance(db_session, centre.id) == 500.0
    assert selected.features == PILOT_STUDENT_FEATURES
    assert db_session.query(SchoolPilotGrant).count() == 1


def test_active_pilot_cannot_be_relaunched_or_fund_unscoped_student(db_session):
    centre = Centre(name="Controlled Pilot")
    other_centre = Centre(name="Other School")
    db_session.add_all([centre, other_centre])
    db_session.commit()
    selected = Student(name="Selected", phone="919000000003", centre_id=centre.id)
    other = Student(name="Other", phone="919000000004", centre_id=other_centre.id)
    db_session.add_all([selected, other])
    db_session.commit()

    launch_pilot(db_session, centre, [selected.id], 50.0, 30)

    with pytest.raises(ValueError, match="already has an active pilot"):
        launch_pilot(db_session, centre, [selected.id], 50.0, 30)
    assert cost_tracker.get_balance(db_session, selected.id) == 50.0

    # A stale/expired pilot may be deliberately relaunched, but only with
    # learners that belong to that school.
    centre.pilot_expires_at = centre.pilot_started_at
    db_session.commit()
    with pytest.raises(ValueError, match="every selected student"):
        launch_pilot(db_session, centre, [other.id], 50.0, 30)
    assert school_billing.get_balance(db_session, centre.id) == 500.0
