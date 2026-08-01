import pytest
from fastapi import HTTPException

from app.models.core import Centre, Organization, Student, Teacher
from app.routers.admin import _require_school_management_access, _scoped_students


def _make_org_with_two_schools(db_session):
    org = Organization(name="Bihar Government Programme", org_type="government")
    db_session.add(org)
    db_session.commit()

    school_a = Centre(name="School A", organization_id=org.id)
    school_b = Centre(name="School B", organization_id=org.id)
    unrelated_school = Centre(name="Unrelated School")
    db_session.add_all([school_a, school_b, unrelated_school])
    db_session.commit()

    student_a = Student(name="Student A", phone="919000000001", centre_id=school_a.id)
    student_b = Student(name="Student B", phone="919000000002", centre_id=school_b.id)
    student_unrelated = Student(name="Student Unrelated", phone="919000000003", centre_id=unrelated_school.id)
    db_session.add_all([student_a, student_b, student_unrelated])
    db_session.commit()

    org_admin = Teacher(
        name="Gov Admin", phone="919999999001", role="org_admin", organization_id=org.id,
    )
    db_session.add(org_admin)
    db_session.commit()
    db_session.refresh(org_admin)

    return org, school_a, school_b, unrelated_school, org_admin


def test_org_admin_sees_students_across_every_school_in_their_organization(db_session):
    org, school_a, school_b, unrelated_school, org_admin = _make_org_with_two_schools(db_session)

    names = {s.name for s in _scoped_students(db_session, org_admin).all()}

    assert names == {"Student A", "Student B"}


def test_org_admin_can_manage_a_school_inside_their_organization(db_session):
    org, school_a, school_b, unrelated_school, org_admin = _make_org_with_two_schools(db_session)

    _require_school_management_access(org_admin, school_a)  # must not raise


def test_org_admin_cannot_manage_an_unrelated_school(db_session):
    org, school_a, school_b, unrelated_school, org_admin = _make_org_with_two_schools(db_session)

    with pytest.raises(HTTPException) as exc_info:
        _require_school_management_access(org_admin, unrelated_school)
    assert exc_info.value.status_code == 403


def test_a_plain_school_admin_only_sees_their_own_school(db_session):
    org, school_a, school_b, unrelated_school, org_admin = _make_org_with_two_schools(db_session)
    school_admin = Teacher(name="School Admin", phone="919999999002", role="admin", centre_id=school_a.id)
    db_session.add(school_admin)
    db_session.commit()

    names = {s.name for s in _scoped_students(db_session, school_admin).all()}

    assert names == {"Student A"}


def test_a_plain_school_admin_cannot_manage_another_school_even_in_no_organization(db_session):
    org, school_a, school_b, unrelated_school, org_admin = _make_org_with_two_schools(db_session)
    school_admin = Teacher(name="School Admin", phone="919999999003", role="admin", centre_id=school_a.id)
    db_session.add(school_admin)
    db_session.commit()

    with pytest.raises(HTTPException):
        _require_school_management_access(school_admin, school_b)
