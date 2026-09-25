"""
WhatsApp delivery of a mind map — mirrors test_leads.py's
_handle_message harness (process_message + the Wati senders mocked).
"""
from app.models.core import Centre, Student
from app.services import cost_tracker
from app.services.chat_core import ChatTurnResult


def _enrolled_student(db_session) -> Student:
    centre = Centre(name="Mind Map School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Real Student", phone="919876543077", centre_id=centre.id)
    db_session.add(student)
    db_session.commit()
    cost_tracker.add_trial_credits(db_session, student.id)  # else the credit-exhaustion gate fires first
    return student


async def test_mindmap_topic_is_sent_as_an_image_with_the_reply_as_caption(db_session, monkeypatch):
    from app.routers.whatsapp import _handle_message

    student = _enrolled_student(db_session)

    async def fake_process_message(db, student_, message_text):
        return ChatTurnResult(reply_text="Here's a mind map of photosynthesis.", mindmap_topic="photosynthesis")

    async def fake_build_mindmap_image(db, student_id, topic):
        assert (student_id, topic) == (student.id, "photosynthesis")
        return b"\x89PNG mind map"

    async def fail_generate_image(prompt):
        raise AssertionError("generate_image must not run for a mind map turn")

    sent_images, sent_texts = [], []

    async def fake_send_image(phone, image_bytes, caption=None):
        sent_images.append((phone, image_bytes, caption))
        return {"sent": True}

    async def fake_send_text(phone, text, *args, **kwargs):
        sent_texts.append(text)
        return {"sent": True}

    monkeypatch.setattr("app.routers.whatsapp.process_message", fake_process_message)
    monkeypatch.setattr("app.routers.whatsapp.build_mindmap_image", fake_build_mindmap_image)
    monkeypatch.setattr("app.routers.whatsapp.generate_image", fail_generate_image)
    monkeypatch.setattr("app.routers.whatsapp.send_whatsapp_image", fake_send_image)
    monkeypatch.setattr("app.routers.whatsapp.send_whatsapp_message", fake_send_text)

    payload = {"eventType": "message", "owner": False, "type": "text", "waId": "919876543077", "text": "mind map of photosynthesis"}
    await _handle_message(db_session, payload)

    assert sent_images == [("919876543077", b"\x89PNG mind map", "Here's a mind map of photosynthesis.")]
    assert sent_texts == []  # the image (with caption) was the delivery; no duplicate text fallback


async def test_failed_mindmap_falls_back_to_the_text_reply(db_session, monkeypatch):
    from app.routers.whatsapp import _handle_message

    _enrolled_student(db_session)

    async def fake_process_message(db, student_, message_text):
        return ChatTurnResult(reply_text="Gravity pulls things down.", mindmap_topic="gravity")

    async def fake_build_mindmap_image_fails(db, student_id, topic):
        return None

    sent_images, sent_texts = [], []

    async def fake_send_image(phone, image_bytes, caption=None):
        sent_images.append(image_bytes)
        return {"sent": True}

    async def fake_send_text(phone, text, *args, **kwargs):
        sent_texts.append(text)
        return {"sent": True}

    monkeypatch.setattr("app.routers.whatsapp.process_message", fake_process_message)
    monkeypatch.setattr("app.routers.whatsapp.build_mindmap_image", fake_build_mindmap_image_fails)
    monkeypatch.setattr("app.routers.whatsapp.send_whatsapp_image", fake_send_image)
    monkeypatch.setattr("app.routers.whatsapp.send_whatsapp_message", fake_send_text)

    payload = {"eventType": "message", "owner": False, "type": "text", "waId": "919876543077", "text": "mind map of gravity"}
    await _handle_message(db_session, payload)

    assert sent_images == []
    assert sent_texts == ["Gravity pulls things down."]
