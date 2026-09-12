import asyncio
from types import SimpleNamespace

from app.models.core import Centre, Student
from app.routers import student_app
from app.services import chat_core, cost_tracker, school_billing


def _make_student(db_session):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Test Student", phone="919000000020", centre_id=centre.id, class_="8", board="CBSE")
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def _bypass_billing_gates(monkeypatch):
    monkeypatch.setattr(school_billing, "is_centre_churned", lambda db, cid: False)
    monkeypatch.setattr(school_billing, "is_centre_pilot_expired", lambda db, cid: False)
    monkeypatch.setattr(cost_tracker, "has_credits", lambda db, sid: True)
    monkeypatch.setattr(cost_tracker, "get_balance", lambda db, sid: 42.0)


def test_portal_chat_delivers_generated_image_and_bills_it(db_session, monkeypatch):
    """
    process_web_message's own docstring flags this exact gap: web/app never
    turned a tutor-decided diagram (result.image_prompt) into an actual
    image the way app.routers.whatsapp does. student_app._reply_to_locked
    now calls chat_core.process_message directly so it can see
    image_prompt, generate the image, save it under the static mount, and
    bill it — same as WhatsApp's own flow.
    """
    student = _make_student(db_session)
    _bypass_billing_gates(monkeypatch)

    async def fake_process_message(db, student, message_text):
        return SimpleNamespace(
            reply_text="Here's a diagram of a plant cell.", video=None,
            image_prompt="a labeled diagram of a plant cell for a Class 8 science textbook",
        )
    monkeypatch.setattr(student_app.chat_core, "process_message", fake_process_message)

    async def fake_generate_image(prompt):
        return b"\x89PNG fake bytes"
    monkeypatch.setattr(student_app, "generate_image", fake_generate_image)

    flat_usage_calls = []
    monkeypatch.setattr(
        cost_tracker, "record_flat_usage",
        lambda db, service, sid: flat_usage_calls.append((service, sid)),
    )

    result = asyncio.run(student_app._reply_to_locked(db_session, student, "explain a plant cell"))

    assert result["image_url"] is not None
    assert result["image_url"].startswith("/static/uploads/generated/")
    assert result["reply"] == "Here's a diagram of a plant cell."
    assert flat_usage_calls == [("azure_image", student.id)]


def test_portal_chat_has_no_image_when_tutor_did_not_request_one(db_session, monkeypatch):
    student = _make_student(db_session)
    _bypass_billing_gates(monkeypatch)

    async def fake_process_message(db, student, message_text):
        return SimpleNamespace(reply_text="Just a plain reply.", video=None, image_prompt=None)
    monkeypatch.setattr(student_app.chat_core, "process_message", fake_process_message)

    async def fail_if_called(prompt):
        raise AssertionError("generate_image should not be called when image_prompt is None")
    monkeypatch.setattr(student_app, "generate_image", fail_if_called)

    result = asyncio.run(student_app._reply_to_locked(db_session, student, "hi"))

    assert result["image_url"] is None
    assert result["reply"] == "Just a plain reply."
