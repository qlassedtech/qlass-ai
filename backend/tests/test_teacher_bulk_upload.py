"""
POST /admin/teachers/bulk-upload/confirm — creates teacher accounts from
previewed rows. Confirmed live: phone was never normalized here, unlike
every other account-creation path — a bulk-uploaded teacher's phone was
stored exactly as typed (e.g. "9199911040") instead of the 91-prefixed
form every login/lookup path normalizes to before querying, meaning that
teacher could never actually log in or receive a WhatsApp message.
"""
from app.models.core import Centre, Teacher
from app.routers.admin import ConfirmTeacherBulkUploadRequest, confirm_teacher_bulk_upload


def _make_admin(db_session):
    centre = Centre(name="Teacher Bulk Test School")
    db_session.add(centre)
    db_session.commit()
    admin = Teacher(name="Admin", phone="919000000091", centre_id=centre.id, role="admin")
    db_session.add(admin)
    db_session.commit()
    return admin, centre


def test_bulk_created_teacher_phone_is_normalized(db_session):
    admin, centre = _make_admin(db_session)
    row = {"name": "New Teacher", "phone": "9199911040", "role": "teacher"}  # bare 10-digit, as typed in a source file

    result = confirm_teacher_bulk_upload(ConfirmTeacherBulkUploadRequest(rows=[row]), db_session, admin)

    assert result["created"] == ["919199911040"]
    teacher = db_session.query(Teacher).filter(Teacher.phone == "919199911040").first()
    assert teacher is not None
    assert db_session.query(Teacher).filter(Teacher.phone == "9199911040").count() == 0


def test_bulk_upload_dedup_matches_regardless_of_source_phone_format(db_session):
    """
    An existing teacher's phone is always stored normalized — a batch row
    listing the SAME person with a bare 10-digit number must still be
    recognized as a duplicate, not silently create a second account.
    """
    admin, centre = _make_admin(db_session)
    db_session.add(Teacher(name="Existing", phone="919199911041", centre_id=centre.id, role="teacher"))
    db_session.commit()

    row = {"name": "Existing", "phone": "9199911041", "role": "teacher"}
    result = confirm_teacher_bulk_upload(ConfirmTeacherBulkUploadRequest(rows=[row]), db_session, admin)

    assert result["created"] == []
    assert result["skipped_count"] == 1
    assert db_session.query(Teacher).filter(Teacher.phone == "919199911041").count() == 1
