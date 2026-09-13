"""
Tests for the classroom/cohort feature (see app.models.core.Classroom and
the /admin/classrooms endpoints in app.routers.admin) — a teacher groups a
subset of their school's students into a class they can view roster-level
progress for, closing the gap against rivals whose whole model is built
around this. Calls the router functions directly (matches
test_admin_student_list.py's pattern), no HTTP layer/token needed.
"""
import pytest
from fastapi import HTTPException

from app.models.core import Centre, Student, Teacher
from app.routers.admin import (
    assign_classroom_students,
    AssignClassroomStudentsRequest,
    ClassroomCreateRequest,
    ClassroomUpdateRequest,
    create_classroom,
    delete_classroom,
    get_classroom,
    list_classrooms,
    unassign_classroom_student,
    update_classroom,
)


def _make_school(db_session, name="Test School"):
    centre = Centre(name=name)
    db_session.add(centre)
    db_session.commit()
    return centre


def _make_teacher(db_session, centre, phone, role="teacher"):
    teacher = Teacher(name="T", phone=phone, centre_id=centre.id, role=role)
    db_session.add(teacher)
    db_session.commit()
    return teacher


def _make_student(db_session, centre, phone, name="Student"):
    student = Student(name=name, phone=phone, centre_id=centre.id)
    db_session.add(student)
    db_session.commit()
    return student


def test_create_classroom(db_session):
    centre = _make_school(db_session)
    teacher = _make_teacher(db_session, centre, "919000000010")

    result = create_classroom(
        ClassroomCreateRequest(name="Class 10A Physics", board="CBSE", class_="10", subject="Physics"),
        db=db_session, teacher=teacher,
    )

    assert result["name"] == "Class 10A Physics"
    assert result["centre_id"] == centre.id
    assert result["teacher_id"] == teacher.id
    assert result["class"] == "10"
    assert result["student_count"] == 0


def test_list_classrooms_scoped_to_own_centre(db_session):
    school_a = _make_school(db_session, "School A")
    school_b = _make_school(db_session, "School B")
    teacher_a = _make_teacher(db_session, school_a, "919000000011")
    teacher_b = _make_teacher(db_session, school_b, "919000000012")

    create_classroom(ClassroomCreateRequest(name="A's Class"), db=db_session, teacher=teacher_a)
    create_classroom(ClassroomCreateRequest(name="B's Class"), db=db_session, teacher=teacher_b)

    result_a = list_classrooms(db=db_session, teacher=teacher_a)
    assert len(result_a) == 1
    assert result_a[0]["name"] == "A's Class"

    result_b = list_classrooms(db=db_session, teacher=teacher_b)
    assert len(result_b) == 1
    assert result_b[0]["name"] == "B's Class"


def test_teacher_from_other_centre_cannot_view_classroom(db_session):
    school_a = _make_school(db_session, "School A")
    school_b = _make_school(db_session, "School B")
    teacher_a = _make_teacher(db_session, school_a, "919000000013")
    teacher_b = _make_teacher(db_session, school_b, "919000000014")

    classroom = create_classroom(ClassroomCreateRequest(name="A's Class"), db=db_session, teacher=teacher_a)

    with pytest.raises(HTTPException) as exc_info:
        get_classroom(classroom["id"], db=db_session, teacher=teacher_b)
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        update_classroom(
            classroom["id"], ClassroomUpdateRequest(name="Hijacked"), db=db_session, teacher=teacher_b,
        )
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        delete_classroom(classroom["id"], db=db_session, teacher=teacher_b)
    assert exc_info.value.status_code == 404


def test_get_classroom_returns_roster_and_analytics(db_session):
    centre = _make_school(db_session)
    teacher = _make_teacher(db_session, centre, "919000000015")
    classroom = create_classroom(ClassroomCreateRequest(name="Class 9B"), db=db_session, teacher=teacher)

    s1 = _make_student(db_session, centre, "919000000016", "Alice")
    s2 = _make_student(db_session, centre, "919000000017", "Bob")
    s1.classroom_id = classroom["id"]
    s2.classroom_id = classroom["id"]
    db_session.commit()
    # A third student at the same school, not in this classroom, must not
    # leak into the roster or analytics.
    _make_student(db_session, centre, "919000000018", "Charlie")

    result = get_classroom(classroom["id"], db=db_session, teacher=teacher)

    assert result["classroom"]["student_count"] == 2
    assert {s["name"] for s in result["students"]} == {"Alice", "Bob"}
    assert result["analytics"]["total_students"] == 2


def test_assign_and_unassign_students(db_session):
    centre = _make_school(db_session)
    teacher = _make_teacher(db_session, centre, "919000000019")
    classroom = create_classroom(ClassroomCreateRequest(name="Class 9B"), db=db_session, teacher=teacher)
    student = _make_student(db_session, centre, "919000000020", "Alice")

    result = assign_classroom_students(
        classroom["id"], AssignClassroomStudentsRequest(student_ids=[student.id]), db=db_session, teacher=teacher,
    )
    assert result["assigned"] == [student.id]
    db_session.refresh(student)
    assert student.classroom_id == classroom["id"]

    unassign_classroom_student(classroom["id"], student.id, db=db_session, teacher=teacher)
    db_session.refresh(student)
    assert student.classroom_id is None


def test_assign_rejects_student_from_a_different_centre(db_session):
    school_a = _make_school(db_session, "School A")
    school_b = _make_school(db_session, "School B")
    teacher_a = _make_teacher(db_session, school_a, "919000000021")
    classroom = create_classroom(ClassroomCreateRequest(name="A's Class"), db=db_session, teacher=teacher_a)
    other_student = _make_student(db_session, school_b, "919000000022", "Outsider")

    # A super_admin can see students from any school, so the endpoint's own
    # centre-membership check (not just visibility scoping) is what must
    # reject this cross-school assignment.
    super_admin = Teacher(name="SA", phone="919000000023", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        assign_classroom_students(
            classroom["id"], AssignClassroomStudentsRequest(student_ids=[other_student.id]),
            db=db_session, teacher=super_admin,
        )
    assert exc_info.value.status_code == 400


def test_unassign_rejects_student_not_in_this_classroom(db_session):
    centre = _make_school(db_session)
    teacher = _make_teacher(db_session, centre, "919000000024")
    classroom = create_classroom(ClassroomCreateRequest(name="Class 9B"), db=db_session, teacher=teacher)
    student = _make_student(db_session, centre, "919000000025", "Alice")  # never assigned

    with pytest.raises(HTTPException) as exc_info:
        unassign_classroom_student(classroom["id"], student.id, db=db_session, teacher=teacher)
    assert exc_info.value.status_code == 404


def test_delete_classroom_nulls_out_students_classroom_id(db_session):
    centre = _make_school(db_session)
    teacher = _make_teacher(db_session, centre, "919000000026")
    classroom = create_classroom(ClassroomCreateRequest(name="Class 9B"), db=db_session, teacher=teacher)
    student = _make_student(db_session, centre, "919000000027", "Alice")
    student.classroom_id = classroom["id"]
    db_session.commit()

    delete_classroom(classroom["id"], db=db_session, teacher=teacher)

    db_session.refresh(student)
    assert student.classroom_id is None
    assert list_classrooms(db=db_session, teacher=teacher) == []


def test_update_classroom_edits_fields(db_session):
    centre = _make_school(db_session)
    teacher = _make_teacher(db_session, centre, "919000000028")
    classroom = create_classroom(
        ClassroomCreateRequest(name="Class 9B", board="CBSE", class_="9", subject="Maths"),
        db=db_session, teacher=teacher,
    )

    result = update_classroom(
        classroom["id"], ClassroomUpdateRequest(name="Class 9B Advanced", subject="Physics"),
        db=db_session, teacher=teacher,
    )

    assert result["name"] == "Class 9B Advanced"
    assert result["subject"] == "Physics"
    # Fields not passed in the PATCH body stay unchanged.
    assert result["board"] == "CBSE"
    assert result["class"] == "9"
