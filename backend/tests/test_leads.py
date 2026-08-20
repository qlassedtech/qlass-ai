"""
API for an external lead-nurture portal to send/receive WhatsApp messages
through this same number, entirely outside the AI tutor — see
app.routers.leads and the early lead-routing check in app.routers.whatsapp.
"""
import pytest
from fastapi import HTTPException

from app.config import settings
from app.models.core import Centre, Lead, Student
from app.services import cost_tracker
from app.services.chat_core import ChatTurnResult
from app.routers.leads import (
    RegisterLeadRequest, SendLeadMessageRequest,
    register_lead, list_leads, release_lead, send_lead_message, require_leads_api_key,
)


def test_require_leads_api_key_rejects_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "leads_api_key", None)
    with pytest.raises(HTTPException) as exc_info:
        require_leads_api_key(authorization="Bearer anything")
    assert exc_info.value.status_code == 503


def test_require_leads_api_key_rejects_missing_or_wrong_key(monkeypatch):
    monkeypatch.setattr(settings, "leads_api_key", "real-key")
    with pytest.raises(HTTPException) as exc_info:
        require_leads_api_key(authorization=None)
    assert exc_info.value.status_code == 401
    with pytest.raises(HTTPException) as exc_info:
        require_leads_api_key(authorization="Bearer wrong-key")
    assert exc_info.value.status_code == 401


def test_require_leads_api_key_accepts_correct_key(monkeypatch):
    monkeypatch.setattr(settings, "leads_api_key", "real-key")
    require_leads_api_key(authorization="Bearer real-key")  # no raise


def test_register_lead_creates_and_upserts(db_session):
    created = register_lead(RegisterLeadRequest(phone="9198765430XX".replace("XX", "01"), name="Priya"), db_session)
    assert created["name"] == "Priya"
    lead_count = db_session.query(Lead).filter(Lead.phone == created["phone"]).count()
    assert lead_count == 1

    # Idempotent — calling again for the same phone updates the same row.
    updated = register_lead(
        RegisterLeadRequest(phone="9198765430XX".replace("XX", "01"), name="Priya Sharma", external_ref="crm-42"),
        db_session,
    )
    assert updated["name"] == "Priya Sharma"
    assert updated["external_ref"] == "crm-42"
    assert db_session.query(Lead).filter(Lead.phone == created["phone"]).count() == 1


def test_list_leads_returns_registered_leads(db_session):
    register_lead(RegisterLeadRequest(phone="9198765430XX".replace("XX", "02")), db_session)
    result = list_leads(db_session)
    assert any(l["phone"] == "919876543002" for l in result)


def test_release_lead_removes_row(db_session):
    created = register_lead(RegisterLeadRequest(phone="9198765430XX".replace("XX", "03")), db_session)
    result = release_lead(created["phone"], db_session)
    assert result == {"released": True}
    assert db_session.query(Lead).filter(Lead.phone == created["phone"]).count() == 0


def test_release_lead_404s_for_unregistered_phone(db_session):
    with pytest.raises(HTTPException) as exc_info:
        release_lead("919000000000", db_session)
    assert exc_info.value.status_code == 404


async def test_send_lead_message_requires_registered_lead(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await send_lead_message("919000000001", SendLeadMessageRequest(message="hi"), db_session)
    assert exc_info.value.status_code == 404


async def test_send_lead_message_requires_exactly_one_of_message_or_template(db_session):
    created = register_lead(RegisterLeadRequest(phone="9198765430XX".replace("XX", "04")), db_session)
    with pytest.raises(HTTPException) as exc_info:
        await send_lead_message(created["phone"], SendLeadMessageRequest(), db_session)
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        await send_lead_message(
            created["phone"], SendLeadMessageRequest(message="hi", template_name="x"), db_session,
        )
    assert exc_info.value.status_code == 400


async def test_send_lead_message_sends_session_message(db_session, monkeypatch):
    created = register_lead(RegisterLeadRequest(phone="9198765430XX".replace("XX", "05")), db_session)
    sent = []

    async def fake_send(to_phone, text):
        sent.append((to_phone, text))
        return {"sent": True}

    monkeypatch.setattr("app.routers.leads.send_whatsapp_message", fake_send)
    result = await send_lead_message(created["phone"], SendLeadMessageRequest(message="Hi there!"), db_session)
    assert result == {"sent": True}
    assert sent == [(created["phone"], "Hi there!")]


async def test_send_lead_message_surfaces_send_failure(db_session, monkeypatch):
    created = register_lead(RegisterLeadRequest(phone="9198765430XX".replace("XX", "06")), db_session)

    async def fake_send(to_phone, text):
        return {"sent": False, "reason": "Wati reports this isn't a valid WhatsApp number"}

    monkeypatch.setattr("app.routers.leads.send_whatsapp_message", fake_send)
    with pytest.raises(HTTPException) as exc_info:
        await send_lead_message(created["phone"], SendLeadMessageRequest(message="Hi there!"), db_session)
    assert exc_info.value.status_code == 502


async def test_whatsapp_message_from_registered_lead_never_reaches_the_tutor(db_session, monkeypatch):
    """
    The whole point of registering a lead — see the early check in
    app.routers.whatsapp._handle_message, right before the cold-start
    signup logic. A registered lead's message must be forwarded to the
    portal and never create a Student or call process_message.
    """
    from app.routers.whatsapp import _handle_message

    monkeypatch.setattr(settings, "leads_api_key", "test-key")  # else the routing check no-ops entirely
    register_lead(RegisterLeadRequest(phone="919876543099"), db_session)

    forwarded = []

    async def fake_forward(lead, message_text, raw_payload):
        forwarded.append((lead.phone, message_text))

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("a registered lead's message must never reach the AI tutor")

    monkeypatch.setattr("app.routers.whatsapp.forward_lead_message_to_portal", fake_forward)
    monkeypatch.setattr("app.routers.whatsapp.process_message", fail_if_called)

    payload = {"eventType": "message", "owner": False, "type": "text", "waId": "919876543099", "text": "Tell me more"}
    await _handle_message(db_session, payload)

    assert forwarded == [("919876543099", "Tell me more")]
    assert db_session.query(Student).filter(Student.phone == "919876543099").count() == 0


async def test_whatsapp_message_from_already_enrolled_student_ignores_lead_registration(db_session, monkeypatch):
    """
    A phone that's ALSO (mistakenly, or post-conversion) registered as a
    lead must not have its real tutor access cut off — see Lead's own
    docstring on why this check is skipped for a known Student/Teacher.
    """
    from app.routers.whatsapp import _handle_message

    centre = Centre(name="Lead Overlap School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Real Student", phone="919876543098", centre_id=centre.id)
    db_session.add(student)
    db_session.commit()
    cost_tracker.add_trial_credits(db_session, student.id)  # else the credit-exhaustion gate fires first
    monkeypatch.setattr(settings, "leads_api_key", "test-key")  # else the routing check no-ops entirely
    register_lead(RegisterLeadRequest(phone="919876543098"), db_session)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("an already-enrolled student must never be diverted to the lead portal")

    called = {"process_message": False}

    async def fake_process_message(db, student, message_text):
        called["process_message"] = True
        return ChatTurnResult(reply_text="ok")

    async def fake_send(*args, **kwargs):
        return {"sent": True}

    monkeypatch.setattr("app.routers.whatsapp.forward_lead_message_to_portal", fail_if_called)
    monkeypatch.setattr("app.routers.whatsapp.process_message", fake_process_message)
    monkeypatch.setattr("app.routers.whatsapp.send_whatsapp_message", fake_send)

    payload = {"eventType": "message", "owner": False, "type": "text", "waId": "919876543098", "text": "hi"}
    await _handle_message(db_session, payload)

    assert called["process_message"] is True
