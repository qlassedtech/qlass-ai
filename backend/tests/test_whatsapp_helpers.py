import asyncio

from app.models.core import Centre, Student
from app.routers.whatsapp import MENU_BUTTON_TO_COMMAND, MENU_BUTTONS_BASE, _resolve_active_student


def test_every_base_menu_button_has_a_command_mapping():
    for button in MENU_BUTTONS_BASE:
        assert button in MENU_BUTTON_TO_COMMAND


def test_talk_to_teacher_is_not_a_proactive_menu_option():
    """
    Explicitly asserts the product decision: the AI tutor shouldn't offer a
    human-escalation button on its own default menu (undercuts its own
    credibility) — that path stays reachable only via an explicit typed
    request or the automatic hint-streak escalation, never a tappable
    button a brand-new student sees immediately.
    """
    assert "🆘 Talk to Teacher" not in MENU_BUTTONS_BASE
    assert "🆘 Talk to Teacher" not in MENU_BUTTON_TO_COMMAND


def test_resolve_active_student_skips_llm_call_for_single_profile_phone(db_session, monkeypatch):
    """
    The actual bug being fixed: a phone with exactly one student profile
    has nothing to disambiguate, but classify_profile_routing (a real,
    billed LLM call) was firing anyway on every single text message —
    including messages from a student already blocked by the credit gate,
    which runs AFTER this function returns. Confirmed live: a student's
    wallet kept draining turn after turn even while every reply was just
    the static "out of credits" notice.
    """
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Solo", phone="919000000099", centre_id=centre.id)
    db_session.add(student)
    db_session.commit()

    async def fail_if_called(message_text, names):
        raise AssertionError("classify_profile_routing should never be called for a single-profile phone")

    monkeypatch.setattr("app.routers.whatsapp.classify_profile_routing", fail_if_called)

    resolved_student, early_reply = asyncio.run(_resolve_active_student(db_session, "919000000099", "what is gravity"))

    assert resolved_student.id == student.id
    assert early_reply is None
