import asyncio

from app.models.core import Centre, Student
from app.routers.whatsapp import (
    MENU_BUTTON_TO_COMMAND,
    MENU_BUTTONS_BASE,
    _create_new_student,
    _extract_school_centre_from_greeting,
    _resolve_active_student,
)
from app.services import tenancy


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


def test_extract_school_centre_matches_name_in_greeting(db_session):
    school = Centre(name="Sunrise Public School")
    db_session.add(school)
    db_session.add(Centre(name=tenancy.QLASS_DIRECT_CENTRE_NAME))
    db_session.commit()

    found = _extract_school_centre_from_greeting(
        db_session, "Hi, I am a student from Sunrise Public School. I am excited to get access to AI tutor"
    )
    assert found is not None
    assert found.id == school.id

    assert _extract_school_centre_from_greeting(db_session, "Hi") is None


def test_extract_school_centre_never_matches_qlass_direct(db_session):
    """
    "Qlass Direct" is an internal fallback label, never a real school a
    student would type — even if it somehow appeared in a message, it must
    not be treated as a school match.
    """
    db_session.add(Centre(name=tenancy.QLASS_DIRECT_CENTRE_NAME))
    db_session.commit()

    assert _extract_school_centre_from_greeting(db_session, "Hi, I am a student from Qlass Direct") is None


def test_create_new_student_attributes_to_school_named_in_greeting(db_session, monkeypatch):
    async def fake_send(phone, body):
        return {"sent": True}

    monkeypatch.setattr("app.routers.whatsapp.send_whatsapp_message", fake_send)
    tenancy._qlass_direct_centre_id = None
    db_session.add(Centre(name=tenancy.QLASS_DIRECT_CENTRE_NAME))
    school = Centre(name="Sunrise Public School", board="CBSE")
    db_session.add(school)
    db_session.commit()

    student = asyncio.run(_create_new_student(
        db_session, "919000000098", "Hi, I am a student from Sunrise Public School. I am excited to get access to AI tutor"
    ))

    assert student.centre_id == school.id
    assert student.board == "CBSE"


def test_create_new_student_falls_back_to_qlass_direct_without_a_school_name(db_session, monkeypatch):
    async def fake_send(phone, body):
        return {"sent": True}

    monkeypatch.setattr("app.routers.whatsapp.send_whatsapp_message", fake_send)
    tenancy._qlass_direct_centre_id = None
    qlass_direct = Centre(name=tenancy.QLASS_DIRECT_CENTRE_NAME)
    db_session.add(qlass_direct)
    db_session.commit()

    student = asyncio.run(_create_new_student(db_session, "919000000097", "Hi"))

    assert student.centre_id == qlass_direct.id
