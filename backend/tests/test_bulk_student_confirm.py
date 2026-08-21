"""
POST /admin/students/bulk-upload/confirm — actually creates/updates
students from previewed rows. Covers the extended field set (email,
gender, parent_name, parent_phone) added alongside the school's-own-CSV
extraction fallback, since Student.email and Parent.phone are both
globally unique and a batch upload must never fail a whole row over one
conflicting field.
"""
from app.models.core import Centre, Parent, Student, Teacher
from app.routers.admin import ConfirmStudentBulkUploadRequest, confirm_student_bulk_upload
from app.services import cost_tracker


def _make_admin(db_session, centre_name="Bulk Confirm School"):
    centre = Centre(name=centre_name)
    db_session.add(centre)
    db_session.commit()
    teacher = Teacher(name="Admin", phone="919000000090", centre_id=centre.id, role="admin")
    db_session.add(teacher)
    db_session.commit()
    return teacher, centre


def test_confirm_creates_student_with_email_gender_and_linked_parent(db_session):
    teacher, centre = _make_admin(db_session)
    row = {
        "name": "Aman Kumar", "phone": "919000000001", "class": "10", "board": "BSEB",
        "email": "aman@example.com", "gender": "male", "parent_name": "Ramesh Kumar", "parent_phone": "919000000101",
    }

    result = confirm_student_bulk_upload(ConfirmStudentBulkUploadRequest(rows=[row]), db_session, teacher)

    assert result["created"] == ["919000000001"]
    student = db_session.query(Student).filter(Student.phone == "919000000001").first()
    assert student.email == "aman@example.com"
    assert student.gender == "male"
    assert cost_tracker.get_balance(db_session, student.id) == cost_tracker.TRIAL_CREDITS
    parent = db_session.query(Parent).filter(Parent.student_id == student.id).first()
    assert parent is not None
    assert parent.phone == "919000000101"
    assert parent.name == "Ramesh Kumar"


def test_confirm_siblings_sharing_a_parent_phone_only_links_the_first(db_session):
    """
    Parent.phone is globally unique — two siblings uploaded in the same
    batch can't both own a Parent row for the same number. The second
    sibling must still be created (name/phone/class intact), just without
    a Parent link, not fail the whole row.
    """
    teacher, centre = _make_admin(db_session)
    rows = [
        {"name": "Aman Kumar", "phone": "919000000002", "parent_name": "Ramesh Kumar", "parent_phone": "919000000102"},
        {"name": "Priya Kumar", "phone": "919000000003", "parent_name": "Ramesh Kumar", "parent_phone": "919000000102"},
    ]

    result = confirm_student_bulk_upload(ConfirmStudentBulkUploadRequest(rows=rows), db_session, teacher)

    assert set(result["created"]) == {"919000000002", "919000000003"}
    aman = db_session.query(Student).filter(Student.phone == "919000000002").first()
    priya = db_session.query(Student).filter(Student.phone == "919000000003").first()
    assert db_session.query(Parent).filter(Parent.phone == "919000000102").count() == 1
    linked_student_id = db_session.query(Parent).filter(Parent.phone == "919000000102").first().student_id
    assert linked_student_id in (aman.id, priya.id)
    assert db_session.query(Parent).filter(Parent.student_id == priya.id).count() + \
        db_session.query(Parent).filter(Parent.student_id == aman.id).count() == 1


def test_confirm_email_already_used_by_a_different_student_is_left_unset(db_session):
    teacher, centre = _make_admin(db_session)
    other_centre = Centre(name="Other School")
    db_session.add(other_centre)
    db_session.commit()
    existing = Student(name="Existing Student", phone="919000000200", email="shared@example.com", centre_id=other_centre.id)
    db_session.add(existing)
    db_session.commit()

    row = {"name": "Aman Kumar", "phone": "919000000004", "email": "shared@example.com"}
    result = confirm_student_bulk_upload(ConfirmStudentBulkUploadRequest(rows=[row]), db_session, teacher)

    assert result["created"] == ["919000000004"]
    new_student = db_session.query(Student).filter(Student.phone == "919000000004").first()
    assert new_student.email is None  # conflict silently skipped, student still created


def test_confirm_updates_email_and_gender_on_an_existing_student(db_session):
    teacher, centre = _make_admin(db_session)
    student = Student(name="Aman Kumar", phone="919000000005", centre_id=centre.id)
    db_session.add(student)
    db_session.commit()

    row = {"name": "Aman Kumar", "phone": "919000000005", "email": "aman2@example.com", "gender": "male"}
    result = confirm_student_bulk_upload(ConfirmStudentBulkUploadRequest(rows=[row]), db_session, teacher)

    assert result["updated"] == ["919000000005"]
    db_session.refresh(student)
    assert student.email == "aman2@example.com"
    assert student.gender == "male"
