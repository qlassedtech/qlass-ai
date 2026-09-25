"""
Regression tests for the Sept 2026 audit batch on app.routers.admin:

  - P1  POST /admin/students must never create a centre-less (orphan)
        student for org_admin/super_admin — it resolves a real centre the
        same way bulk-upload/confirm does, or 400s.
  - P1  Presentation generation is recorded as a PresentationJob; the
        status endpoint is centre-scoped (cross-tenant 404) and bills the
        job's own centre exactly once.
  - P2  Digest/payment-link may only go to the student's own phone,
        whatsapp_phone, or linked parent's phone.
"""
import pytest
from fastapi import HTTPException

from app.models.core import AuditLog, Centre, Organization, Parent, PresentationJob, SchoolCreditEvent, Student, Teacher
from app.routers import admin
from app.routers.admin import (
    DigestRequest, PresentationGenerateRequest, SendPaymentLinkRequest, StudentCreateRequest, create_student,
    generate_presentation, presentation_status, send_digest, send_payment_link,
)
from app.services import cost_tracker, school_billing


def _make_org(db_session, name="Org"):
    org = Organization(name=name)
    db_session.add(org)
    db_session.commit()
    return org


def _make_centre(db_session, name="School", organization_id=None):
    centre = Centre(name=name, organization_id=organization_id)
    db_session.add(centre)
    db_session.commit()
    return centre


_phone_counter = iter(range(919000001000, 919000009999))


def _make_teacher(db_session, role="admin", centre_id=None, organization_id=None):
    teacher = Teacher(
        name=role, phone=str(next(_phone_counter)), role=role, centre_id=centre_id, organization_id=organization_id,
    )
    db_session.add(teacher)
    db_session.commit()
    return teacher


# --- P1: orphan students -----------------------------------------------------

def test_org_admin_create_student_without_centre_id_is_400_and_creates_nothing(db_session):
    org = _make_org(db_session)
    _make_centre(db_session, organization_id=org.id)
    org_admin = _make_teacher(db_session, role="org_admin", organization_id=org.id)

    with pytest.raises(HTTPException) as exc_info:
        create_student(StudentCreateRequest(name="Orphan", phone="9876500001"), db=db_session, teacher=org_admin)
    assert exc_info.value.status_code == 400
    assert db_session.query(Student).count() == 0


def test_org_admin_create_student_into_own_org_centre(db_session):
    org = _make_org(db_session)
    centre = _make_centre(db_session, organization_id=org.id)
    org_admin = _make_teacher(db_session, role="org_admin", organization_id=org.id)

    created = create_student(
        StudentCreateRequest(name="Asha", phone="9876500002", centre_id=centre.id), db=db_session, teacher=org_admin,
    )

    student = db_session.query(Student).filter(Student.id == created["id"]).first()
    assert student.centre_id == centre.id
    assert cost_tracker.get_balance(db_session, student.id) == cost_tracker.TRIAL_CREDITS
    audit = db_session.query(AuditLog).filter(AuditLog.action == "create_student").one()
    assert f"centre_id={centre.id}" in audit.detail


def test_org_admin_create_student_into_foreign_centre_is_403(db_session):
    org = _make_org(db_session)
    other_org = _make_org(db_session, name="Other Org")
    foreign_centre = _make_centre(db_session, name="Foreign", organization_id=other_org.id)
    org_admin = _make_teacher(db_session, role="org_admin", organization_id=org.id)

    with pytest.raises(HTTPException) as exc_info:
        create_student(
            StudentCreateRequest(name="Nope", phone="9876500003", centre_id=foreign_centre.id),
            db=db_session, teacher=org_admin,
        )
    assert exc_info.value.status_code == 403
    assert db_session.query(Student).count() == 0


def test_school_admin_create_student_ignores_centre_id_and_uses_own(db_session):
    own = _make_centre(db_session, name="Own")
    other = _make_centre(db_session, name="Other")
    school_admin = _make_teacher(db_session, role="admin", centre_id=own.id)

    created = create_student(
        StudentCreateRequest(name="Asha", phone="9876500004", centre_id=other.id), db=db_session, teacher=school_admin,
    )
    assert db_session.query(Student).filter(Student.id == created["id"]).first().centre_id == own.id


# --- P1/M1: presentation jobs -------------------------------------------------

@pytest.fixture()
def gamma(monkeypatch):
    """Fake Gamma: generate returns a fixed id, status is controlled by the test."""
    state = {"status": "pending", "credits_deducted": None, "status_calls": 0}
    monkeypatch.setattr(admin.settings, "gamma_api_key", "test-key")

    async def fake_create(topic, num_cards=8):
        return "gen_test_123"

    async def fake_status(generation_id):
        state["status_calls"] += 1
        return {"status": state["status"], "url": "https://gamma.app/x", "credits_deducted": state["credits_deducted"]}

    monkeypatch.setattr(admin, "create_presentation_generation", fake_create)
    monkeypatch.setattr(admin, "get_generation_status", fake_status)
    return state


async def test_generate_presentation_records_job_and_audit_row(db_session, gamma):
    centre = _make_centre(db_session)
    school_billing.add_credits(db_session, centre.id, 100.0)
    teacher = _make_teacher(db_session, role="teacher", centre_id=centre.id)

    result = await generate_presentation(PresentationGenerateRequest(topic="Real Numbers"), db=db_session, teacher=teacher)

    assert result == {"generation_id": "gen_test_123"}
    job = db_session.query(PresentationJob).filter(PresentationJob.generation_id == "gen_test_123").one()
    assert job.centre_id == centre.id
    assert job.teacher_id == teacher.id
    assert job.billed is False
    audit = db_session.query(AuditLog).filter(AuditLog.action == "generate_presentation").one()
    assert audit.target_id == centre.id
    assert "gen_test_123" in audit.detail


async def test_org_admin_generate_presentation_bills_named_centre_not_null(db_session, gamma):
    org = _make_org(db_session)
    centre = _make_centre(db_session, organization_id=org.id)
    school_billing.add_credits(db_session, centre.id, 100.0)
    org_admin = _make_teacher(db_session, role="org_admin", organization_id=org.id)

    with pytest.raises(HTTPException) as exc_info:
        await generate_presentation(PresentationGenerateRequest(topic="X"), db=db_session, teacher=org_admin)
    assert exc_info.value.status_code == 400

    await generate_presentation(PresentationGenerateRequest(topic="X", centre_id=centre.id), db=db_session, teacher=org_admin)
    job = db_session.query(PresentationJob).one()
    assert job.centre_id == centre.id

    gamma["status"], gamma["credits_deducted"] = "completed", 13
    await presentation_status("gen_test_123", db=db_session, teacher=org_admin)
    event = db_session.query(SchoolCreditEvent).filter(SchoolCreditEvent.service == school_billing.GAMMA_PRESENTATION_SERVICE).one()
    assert event.centre_id == centre.id


async def test_presentation_status_is_404_for_another_school_and_unknown_ids(db_session, gamma):
    centre_a = _make_centre(db_session, name="A")
    centre_b = _make_centre(db_session, name="B")
    school_billing.add_credits(db_session, centre_a.id, 100.0)
    teacher_a = _make_teacher(db_session, role="teacher", centre_id=centre_a.id)
    teacher_b = _make_teacher(db_session, role="admin", centre_id=centre_b.id)
    await generate_presentation(PresentationGenerateRequest(topic="X"), db=db_session, teacher=teacher_a)

    with pytest.raises(HTTPException) as exc_info:
        await presentation_status("gen_test_123", db=db_session, teacher=teacher_b)
    assert exc_info.value.status_code == 404
    with pytest.raises(HTTPException) as exc_info:
        await presentation_status("gen_never_started", db=db_session, teacher=teacher_a)
    assert exc_info.value.status_code == 404
    assert gamma["status_calls"] == 0  # scoping is checked before Gamma is ever contacted

    ok = await presentation_status("gen_test_123", db=db_session, teacher=teacher_a)
    assert ok["status"] == "pending"


async def test_presentation_status_visible_to_org_admin_and_super_admin(db_session, gamma):
    org = _make_org(db_session)
    centre = _make_centre(db_session, organization_id=org.id)
    school_billing.add_credits(db_session, centre.id, 100.0)
    teacher = _make_teacher(db_session, role="teacher", centre_id=centre.id)
    org_admin = _make_teacher(db_session, role="org_admin", organization_id=org.id)
    other_org_admin = _make_teacher(db_session, role="org_admin", organization_id=_make_org(db_session, "Other").id)
    super_admin = _make_teacher(db_session, role="super_admin")
    await generate_presentation(PresentationGenerateRequest(topic="X"), db=db_session, teacher=teacher)

    assert (await presentation_status("gen_test_123", db=db_session, teacher=org_admin))["status"] == "pending"
    assert (await presentation_status("gen_test_123", db=db_session, teacher=super_admin))["status"] == "pending"
    with pytest.raises(HTTPException) as exc_info:
        await presentation_status("gen_test_123", db=db_session, teacher=other_org_admin)
    assert exc_info.value.status_code == 404


async def test_presentation_completed_is_billed_to_job_centre_exactly_once(db_session, gamma):
    centre = _make_centre(db_session)
    school_billing.add_credits(db_session, centre.id, 100.0)
    teacher = _make_teacher(db_session, role="teacher", centre_id=centre.id)
    await generate_presentation(PresentationGenerateRequest(topic="X"), db=db_session, teacher=teacher)

    gamma["status"], gamma["credits_deducted"] = "completed", 13
    for _ in range(3):
        result = await presentation_status("gen_test_123", db=db_session, teacher=teacher)
        assert result["status"] == "completed"

    events = db_session.query(SchoolCreditEvent).filter(SchoolCreditEvent.service == school_billing.GAMMA_PRESENTATION_SERVICE).all()
    assert len(events) == 1
    assert events[0].centre_id == centre.id
    job = db_session.query(PresentationJob).one()
    assert job.billed is True
    assert job.status == "completed"
    expected_raw = 13 * school_billing.GAMMA_CREDIT_INR
    assert school_billing.get_balance(db_session, centre.id) == round(100.0 - expected_raw * school_billing.MARKUP_MULTIPLIER, 10)


# --- P2: digest / payment link recipients -------------------------------------

@pytest.fixture()
def outbox(monkeypatch):
    sent = []

    async def fake_send(to_phone, body):
        sent.append(to_phone)
        return {"sent": True}

    monkeypatch.setattr(admin, "send_whatsapp_message", fake_send)
    return sent


def _make_student_with_parent(db_session, centre_id):
    student = Student(name="Asha", phone="919876500010", whatsapp_phone="919876500011", centre_id=centre_id)
    db_session.add(student)
    db_session.commit()
    db_session.add(Parent(student_id=student.id, name="Parent", phone="919876500012"))
    db_session.commit()
    return student


async def test_digest_to_arbitrary_phone_is_400(db_session, outbox):
    centre = _make_centre(db_session)
    teacher = _make_teacher(db_session, role="teacher", centre_id=centre.id)
    student = _make_student_with_parent(db_session, centre.id)

    with pytest.raises(HTTPException) as exc_info:
        await send_digest(student.id, DigestRequest(to_phone="919999999999"), db=db_session, teacher=teacher)
    assert exc_info.value.status_code == 400
    assert outbox == []


@pytest.mark.parametrize("to_phone", ["919876500010", "9876500011", "+91 98765 00012"])
async def test_digest_to_student_whatsapp_or_parent_phone_is_allowed(db_session, outbox, to_phone):
    centre = _make_centre(db_session)
    teacher = _make_teacher(db_session, role="teacher", centre_id=centre.id)
    student = _make_student_with_parent(db_session, centre.id)

    result = await send_digest(student.id, DigestRequest(to_phone=to_phone), db=db_session, teacher=teacher)
    assert result == {"sent": True}
    assert outbox == [admin.normalize_phone(to_phone)]


async def test_payment_link_to_arbitrary_phone_is_400_and_defaults_to_student(db_session, outbox):
    centre = _make_centre(db_session)
    teacher = _make_teacher(db_session, role="teacher", centre_id=centre.id)
    student = _make_student_with_parent(db_session, centre.id)

    with pytest.raises(HTTPException) as exc_info:
        await send_payment_link(student.id, SendPaymentLinkRequest(to_phone="919999999999"), db=db_session, teacher=teacher)
    assert exc_info.value.status_code == 400
    assert outbox == []

    await send_payment_link(student.id, SendPaymentLinkRequest(), db=db_session, teacher=teacher)
    await send_payment_link(student.id, SendPaymentLinkRequest(to_phone="919876500012"), db=db_session, teacher=teacher)
    assert outbox == ["919876500010", "919876500012"]
