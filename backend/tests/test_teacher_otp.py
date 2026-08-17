"""
WhatsApp OTP login for teachers/admins — sits alongside password login,
not a replacement (see /auth/login). Unlike student OTP, verifying never
creates a new account.
"""
import jwt as pyjwt

from app.config import settings
from app.models.core import Centre, Teacher
from app.routers.admin import TeacherPhoneRequest, VerifyTeacherOtpRequest, request_teacher_otp, verify_teacher_otp
from app.services.teacher_auth import JWT_ALGORITHM


def _make_teacher(db_session, phone="919000000050", phone_verified=True):
    centre = Centre(name="OTP Test School")
    db_session.add(centre)
    db_session.commit()
    teacher = Teacher(name="OTP Teacher", phone=phone, centre_id=centre.id, role="admin", phone_verified=phone_verified)
    db_session.add(teacher)
    db_session.commit()
    return teacher


def _extract_otp(params: list[dict]) -> str:
    # params == [{"name": "1", "value": "123456"}]
    return params[0]["value"]


async def test_request_teacher_otp_sends_code_for_existing_teacher(db_session, monkeypatch):
    teacher = _make_teacher(db_session)
    sent = []

    async def fake_send(to_phone, template_name, params):
        sent.append((to_phone, template_name, params))
        return {"sent": True}

    monkeypatch.setattr("app.routers.admin.send_template_message", fake_send)

    result = await request_teacher_otp(TeacherPhoneRequest(phone=teacher.phone), db_session)

    assert result == {"sent": True}
    assert len(sent) == 1
    assert sent[0][0] == teacher.phone
    assert _extract_otp(sent[0][2]).isdigit()


async def test_request_teacher_otp_rejects_unknown_phone(db_session, monkeypatch):
    async def fail_if_called(to_phone, template_name, params):
        raise AssertionError("should never send a code for a phone with no teacher account")

    monkeypatch.setattr("app.routers.admin.send_template_message", fail_if_called)

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await request_teacher_otp(TeacherPhoneRequest(phone="919000099999"), db_session)
    assert exc_info.value.status_code == 404


async def test_request_teacher_otp_surfaces_send_failure(db_session, monkeypatch):
    teacher = _make_teacher(db_session, phone="919000000053")

    async def fake_send(to_phone, template_name, params):
        return {"sent": False, "reason": "Wati reports this isn't a valid WhatsApp number"}

    monkeypatch.setattr("app.routers.admin.send_template_message", fake_send)

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await request_teacher_otp(TeacherPhoneRequest(phone=teacher.phone), db_session)
    assert exc_info.value.status_code == 502


async def test_verify_teacher_otp_issues_valid_teacher_token(db_session, monkeypatch):
    teacher = _make_teacher(db_session, phone="919000000051")
    sent = []

    async def fake_send(to_phone, template_name, params):
        sent.append(params)
        return {"sent": True}

    monkeypatch.setattr("app.routers.admin.send_template_message", fake_send)
    await request_teacher_otp(TeacherPhoneRequest(phone=teacher.phone), db_session)
    otp = _extract_otp(sent[0])

    result = await verify_teacher_otp(VerifyTeacherOtpRequest(phone=teacher.phone, otp=otp), db_session)

    assert result["teacher"]["id"] == teacher.id
    assert result["teacher"]["role"] == "admin"
    payload = pyjwt.decode(result["access_token"], settings.secret_key, algorithms=[JWT_ALGORITHM])
    assert payload["type"] == "teacher"
    assert payload["sub"] == str(teacher.id)


async def test_verify_teacher_otp_rejects_wrong_code(db_session, monkeypatch):
    teacher = _make_teacher(db_session, phone="919000000052")

    async def fake_send(to_phone, template_name, params):
        return {"sent": True}

    monkeypatch.setattr("app.routers.admin.send_template_message", fake_send)
    await request_teacher_otp(TeacherPhoneRequest(phone=teacher.phone), db_session)

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await verify_teacher_otp(VerifyTeacherOtpRequest(phone=teacher.phone, otp="000000"), db_session)
    assert exc_info.value.status_code == 400


async def test_request_teacher_otp_rejects_unverified_phone(db_session, monkeypatch):
    """
    Teacher.phone_verified is False only for the Google-registration path
    (see app.routers.admin.register_school_verify) — Google proves the
    email, never the phone typed alongside it. Without this check,
    whoever actually controls that unverified number could OTP their way
    into an account that isn't theirs.
    """
    teacher = _make_teacher(db_session, phone="919000000054", phone_verified=False)

    async def fail_if_called(to_phone, template_name, params):
        raise AssertionError("should never send a code for an unverified phone")

    monkeypatch.setattr("app.routers.admin.send_template_message", fail_if_called)

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await request_teacher_otp(TeacherPhoneRequest(phone=teacher.phone), db_session)
    assert exc_info.value.status_code == 403
