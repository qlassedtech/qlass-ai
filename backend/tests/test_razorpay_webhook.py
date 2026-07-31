import asyncio

from app.models.core import Centre, CreditEvent, Student
from app.routers.razorpay_webhook import _handle_charged, _handle_ended
from app.services import cost_tracker


def _make_student(db_session, is_staff_profile=False, razorpay_subscription_id="sub_test123"):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(
        name="Test Student", phone="919000000001", centre_id=centre.id,
        is_staff_profile=is_staff_profile, razorpay_subscription_id=razorpay_subscription_id,
    )
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def _charged_payload(subscription_id: str, payment_id: str, amount_paise: int) -> dict:
    # Field paths taken directly from Razorpay's published webhook docs —
    # see razorpay_webhook.py's module docstring.
    return {
        "event": "subscription.charged",
        "payload": {
            "subscription": {"entity": {"id": subscription_id}},
            "payment": {"entity": {"id": payment_id, "amount": amount_paise}},
        },
    }


def _ended_payload(subscription_id: str) -> dict:
    return {"event": "subscription.cancelled", "payload": {"subscription": {"entity": {"id": subscription_id}}}}


def test_charged_activates_student_annual_plan_and_logs_real_payment(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.routers.razorpay_webhook.send_whatsapp_message",
        lambda phone, message: sent.append((phone, message)) or _resolved({"sent": True}),
    )
    student = _make_student(db_session, is_staff_profile=False)
    payload = _charged_payload("sub_test123", "pay_abc999", 180000)

    asyncio.run(_handle_charged(db_session, payload))

    db_session.refresh(student)
    assert student.subscription_plan == "unlimited"
    assert cost_tracker.is_unlimited_active(student) is True
    event = db_session.query(CreditEvent).filter(CreditEvent.external_ref == "pay_abc999").first()
    assert event is not None
    assert event.amount == 1800.0
    assert event.service == "unlimited_plan_recurring"
    assert len(sent) == 1
    assert sent[0][0] == student.phone


def test_charged_uses_monthly_term_for_a_teacher_profile(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.routers.razorpay_webhook.send_whatsapp_message",
        lambda phone, message: _resolved({"sent": True}),
    )
    student = _make_student(db_session, is_staff_profile=True)
    payload = _charged_payload("sub_test123", "pay_teacher001", 350000)

    asyncio.run(_handle_charged(db_session, payload))

    db_session.refresh(student)
    # A monthly term should land ~30 days out, not ~365 like the student
    # plan — checked as a range rather than an exact value to avoid
    # flakiness from test execution timing.
    from datetime import datetime, timezone
    remaining = (student.subscription_expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days
    assert 25 <= remaining <= 31


def test_charged_is_idempotent_for_a_redelivered_webhook(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.routers.razorpay_webhook.send_whatsapp_message",
        lambda phone, message: calls.append(1) or _resolved({"sent": True}),
    )
    _make_student(db_session)
    payload = _charged_payload("sub_test123", "pay_dup001", 180000)

    asyncio.run(_handle_charged(db_session, payload))
    asyncio.run(_handle_charged(db_session, payload))  # Razorpay redelivers on anything but a clean 2xx

    assert db_session.query(CreditEvent).filter(CreditEvent.external_ref == "pay_dup001").count() == 1
    assert len(calls) == 1  # no duplicate WhatsApp notification either


def test_charged_ignores_unknown_subscription_id(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.routers.razorpay_webhook.send_whatsapp_message",
        lambda phone, message: _resolved({"sent": True}),
    )
    payload = _charged_payload("sub_does_not_exist", "pay_xyz", 180000)
    asyncio.run(_handle_charged(db_session, payload))  # must not raise
    assert db_session.query(CreditEvent).filter(CreditEvent.external_ref == "pay_xyz").count() == 0


def test_ended_clears_subscription_id_but_keeps_access_until_expiry(db_session):
    student = _make_student(db_session)
    student.subscription_plan = "unlimited"
    from datetime import datetime, timedelta, timezone
    student.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=10)
    db_session.commit()

    _handle_ended(db_session, _ended_payload("sub_test123"))

    db_session.refresh(student)
    assert student.razorpay_subscription_id is None
    assert student.subscription_plan == "unlimited"  # unchanged — access continues until it naturally expires
    assert cost_tracker.is_unlimited_active(student) is True


def test_ended_ignores_unknown_subscription_id(db_session):
    _handle_ended(db_session, _ended_payload("sub_does_not_exist"))  # must not raise


class _resolved:
    """A trivial awaitable wrapping a plain value, for mocking send_whatsapp_message (async) with a sync lambda."""

    def __init__(self, value):
        self._value = value

    def __await__(self):
        async def _coro():
            return self._value
        return _coro().__await__()
