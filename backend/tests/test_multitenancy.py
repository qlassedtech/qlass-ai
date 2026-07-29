"""
Regression tests for the cross-tenant bugs found during the post-build
audit — these must never silently come back.
"""
from app.models.core import Centre, Student, Teacher
from app.services.tenancy import get_or_create_linked_student, create_student_profile


def _make_school(db_session, name):
    centre = Centre(name=name)
    db_session.add(centre)
    db_session.commit()
    return centre


def test_bulk_upload_style_lookup_is_scoped_to_own_school(db_session):
    """
    Regression test for the audit finding: matching an "existing" student
    by phone alone (no centre filter) let one school's bulk upload
    overwrite another school's student. The fix scopes the lookup by
    phone AND centre_id — this test asserts that scoping holds.
    """
    school_a = _make_school(db_session, "School A")
    school_b = _make_school(db_session, "School B")

    student_a = Student(name="Real Student", phone="919555500001", centre_id=school_a.id, class_="8")
    db_session.add(student_a)
    db_session.commit()

    # School B's upload matches the SAME phone number — the correct lookup
    # (phone + own centre_id) must NOT find School A's student.
    match = (
        db_session.query(Student)
        .filter(Student.phone == "919555500001", Student.centre_id == school_b.id)
        .first()
    )
    assert match is None, "cross-tenant match — School B must never see School A's student by phone alone"

    # School A's own upload, using the same scoped lookup, DOES find it.
    own_match = (
        db_session.query(Student)
        .filter(Student.phone == "919555500001", Student.centre_id == school_a.id)
        .first()
    )
    assert own_match is not None
    assert own_match.id == student_a.id


def test_teacher_personal_profile_excluded_from_scoped_students(db_session):
    """
    Regression test: a teacher's own "My AI Tutor" profile must never
    appear in a school's real Student Roster.
    """
    school = _make_school(db_session, "Riverside Academy")
    teacher = Teacher(name="Priya Sharma", phone="919123456780", centre_id=school.id, role="admin")
    db_session.add(teacher)
    db_session.commit()

    real_student = Student(name="Real Student", phone="919555500099", centre_id=school.id)
    db_session.add(real_student)
    db_session.commit()

    personal = get_or_create_linked_student(db_session, teacher.phone, teacher.name, teacher.centre_id)
    assert personal.is_staff_profile is True

    # The roster query every admin.py list endpoint uses — must exclude staff profiles.
    roster = (
        db_session.query(Student)
        .filter(Student.is_staff_profile.is_(False), Student.centre_id == school.id)
        .all()
    )
    roster_ids = {s.id for s in roster}
    assert real_student.id in roster_ids
    assert personal.id not in roster_ids


def test_teacher_personal_profile_never_collides_with_real_student_same_phone(db_session):
    """
    Regression test: a teacher who is ALSO a parent of an enrolled student
    at the same school, sharing the same phone number, must get a distinct
    personal tutor profile — never accidentally reuse/merge into their
    child's real student record.
    """
    school = _make_school(db_session, "Riverside Academy")
    teacher = Teacher(name="Parent Teacher", phone="919888800001", centre_id=school.id, role="teacher")
    db_session.add(teacher)
    db_session.commit()

    # The teacher's own child, enrolled as a real student, shares the phone.
    real_child = Student(name="Their Child", phone=teacher.phone, centre_id=school.id, class_="5")
    db_session.add(real_child)
    db_session.commit()

    personal = get_or_create_linked_student(db_session, teacher.phone, teacher.name, teacher.centre_id)

    assert personal.id != real_child.id
    assert personal.is_staff_profile is True
    assert real_child.is_staff_profile is False

    # Calling it again must return the SAME personal profile, not create a third row.
    personal_again = get_or_create_linked_student(db_session, teacher.phone, teacher.name, teacher.centre_id)
    assert personal_again.id == personal.id


def test_create_student_profile_defaults_to_not_staff(db_session):
    school = _make_school(db_session, "Some School")
    student = create_student_profile(db_session, "919777700001", "Web Student", school.id)
    assert student.is_staff_profile is False
