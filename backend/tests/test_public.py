from app.models.core import Centre, Student
from app.routers.public import RegisterRequest, VerifyRegisterRequest, get_school_info, register, register_verify
from app.services import cost_tracker, tenancy


class _FakeRequest:
    """Minimal stand-in for starlette.requests.Request — register() only
    reads .headers/.client.host to key the signup rate limiter (see
    app.services.rate_limit.is_signup_rate_limited), so a direct unit-test
    call doesn't need FastAPI's real request machinery, just this shape."""

    headers: dict = {}
    client = None


def test_get_school_info_returns_name_and_logo(db_session):
    centre = Centre(name="Sunrise Public School", logo_url="/static/logos/sunrise.png")
    db_session.add(centre)
    db_session.commit()

    result = get_school_info("sunrise-public-school", db_session)
    assert result == {"name": "Sunrise Public School", "logo_url": "/static/logos/sunrise.png"}

    assert get_school_info(None, db_session) == {"name": None, "logo_url": None}
    assert get_school_info("no-such-school", db_session) == {"name": None, "logo_url": None}


async def _register_and_verify(db_session, request: RegisterRequest) -> dict:
    """
    Drives the full two-step self-signup flow (see app.routers.public's
    module docstring on register/register_verify): step 1 sends a WhatsApp
    OTP instead of creating the student directly, so this helper captures
    the code from the mocked send_template_message call and immediately
    verifies it, giving tests the same "one call, get a created student"
    shape they had before the OTP step was added.
    """
    captured_otp = {}

    async def fake_send_template(to_phone, template_name, params):
        for param in params:
            if param["name"] == "1":
                captured_otp["code"] = param["value"]
        return {"sent": True}

    import app.routers.public as public_module
    real_send_template = public_module.send_template_message
    public_module.send_template_message = fake_send_template
    try:
        step1 = await register(request, _FakeRequest(), db_session)
        assert step1["success"] is True
        if step1.get("already_registered"):
            return step1
        assert step1["otp_required"] is True
    finally:
        public_module.send_template_message = real_send_template

    return await register_verify(
        VerifyRegisterRequest(**request.model_dump(), otp=captured_otp["code"]), db_session,
    )


async def test_register_creates_student_with_trial_credits_and_full_features(db_session, monkeypatch):
    sent = []

    async def fake_send_template(to_phone, template_name, params):
        sent.append((to_phone, template_name, params))
        return {"sent": True}

    monkeypatch.setattr("app.routers.public.send_template_message", fake_send_template)
    # get_qlass_direct_centre_id caches the id at module scope across
    # calls/tests — the seeded centre from migration 0020 doesn't exist in
    # this per-test in-memory SQLite DB, so create it here under the exact
    # name it looks up by.
    tenancy._qlass_direct_centre_id = None
    db_session.add(Centre(name=tenancy.QLASS_DIRECT_CENTRE_NAME))
    db_session.commit()

    result = await _register_and_verify(db_session, RegisterRequest(name="Nikhil", phone="8888800001"))

    assert result["success"] is True
    assert result["already_registered"] is False
    student = db_session.query(Student).filter(Student.phone == "918888800001").first()
    assert student is not None
    assert student.name == "Nikhil"
    assert cost_tracker.get_balance(db_session, student.id) == cost_tracker.TRIAL_CREDITS
    assert student.has_feature("youtube_videos") is True  # full features, not the conservative default
    # Two template sends now: the OTP itself (step 1) and the welcome
    # message with credits (step 2, after verification).
    assert len(sent) == 2
    welcome_to_phone, welcome_template, welcome_params = sent[1]
    assert welcome_template == "student_signup_activation"
    assert welcome_to_phone == "918888800001"
    assert {"name": "1", "value": "Nikhil"} in welcome_params


async def test_register_links_to_school_via_slug(db_session, monkeypatch):
    tenancy._qlass_direct_centre_id = None
    db_session.add(Centre(name=tenancy.QLASS_DIRECT_CENTRE_NAME))
    school = Centre(name="Sunrise Public School", board="CBSE")
    db_session.add(school)
    db_session.commit()

    result = await _register_and_verify(
        db_session, RegisterRequest(name="Priya", phone="8888800002", school="sunrise-public-school"),
    )

    assert result["success"] is True
    student = db_session.query(Student).filter(Student.phone == "918888800002").first()
    assert student.centre_id == school.id
    assert student.board == "CBSE"


async def test_register_existing_phone_does_not_duplicate_or_regrant_credits(db_session, monkeypatch):
    sent = []

    async def fake_send(phone, body):
        sent.append(body)
        return {"sent": True}

    monkeypatch.setattr("app.routers.public.send_whatsapp_message", fake_send)
    tenancy._qlass_direct_centre_id = None
    centre = Centre(name=tenancy.QLASS_DIRECT_CENTRE_NAME)
    db_session.add(centre)
    db_session.commit()
    existing = tenancy.create_student_profile(db_session, "918888800003", "Existing Student", centre.id)
    balance_before = cost_tracker.get_balance(db_session, existing.id)

    result = await register(RegisterRequest(name="Existing Student", phone="8888800003"), _FakeRequest(), db_session)

    assert result["success"] is True
    assert result["already_registered"] is True
    assert db_session.query(Student).filter(Student.phone == "918888800003").count() == 1
    assert cost_tracker.get_balance(db_session, existing.id) == balance_before  # not re-granted
    assert "already set up" in sent[0]
