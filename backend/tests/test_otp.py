import uuid

from app.services.otp import generate_and_store_otp, verify_otp


def _phone():
    # Unique per test so real Redis state from one test can never leak
    # into another, even when tests run out of order or in parallel.
    return f"9{uuid.uuid4().int % 10**9:09d}"


async def test_generate_and_store_otp_returns_six_digits():
    otp = await generate_and_store_otp("login", _phone())
    assert len(otp) == 6
    assert otp.isdigit()


async def test_verify_otp_succeeds_with_correct_code():
    phone = _phone()
    otp = await generate_and_store_otp("login", phone)
    assert await verify_otp("login", phone, otp) is True


async def test_verify_otp_fails_with_wrong_code():
    phone = _phone()
    await generate_and_store_otp("login", phone)
    assert await verify_otp("login", phone, "000000") is False


async def test_verify_otp_fails_for_unknown_phone():
    assert await verify_otp("login", _phone(), "123456") is False


async def test_verify_otp_is_single_use():
    # A verified OTP must be consumed — otherwise a leaked/observed code
    # (e.g. over someone's shoulder) stays valid for the rest of its TTL.
    phone = _phone()
    otp = await generate_and_store_otp("login", phone)
    assert await verify_otp("login", phone, otp) is True
    assert await verify_otp("login", phone, otp) is False


async def test_otp_purposes_are_isolated():
    # Same phone, two different purposes (e.g. login vs password-reset)
    # must not share or overwrite each other's stored code.
    phone = _phone()
    login_otp = await generate_and_store_otp("login", phone)
    reset_otp = await generate_and_store_otp("password_reset", phone)
    assert await verify_otp("password_reset", phone, login_otp) is False
    assert await verify_otp("login", phone, login_otp) is True
    assert await verify_otp("password_reset", phone, reset_otp) is True
