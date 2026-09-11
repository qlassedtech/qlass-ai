"""
Regression tests for the Sept 2026 security/correctness audit fixes
(money/auth items C-01, C-03, C-05, C-12 plus the IST period boundary,
client IP derivation, webhook fail-closed and failed-login limiter).
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.core import AuditLog, Centre, CreditEvent, Student, Teacher
from app.routers import payments
from app.routers.admin import (
    SchoolCreateOrderRequest, StudentCreateRequest, StudentUpdateRequest, create_school_order, create_student,
    update_student,
)
from app.services import cost_tracker, rate_limit
from app.services.whatsapp_client import verify_webhook_auth


def _make_centre(db_session, name="Test School"):
    centre = Centre(name=name)
    db_session.add(centre)
    db_session.commit()
    return centre


def _make_teacher(db_session, centre_id, role="admin", phone="919000000100"):
    teacher = Teacher(name="Admin", phone=phone, role=role, centre_id=centre_id)
    db_session.add(teacher)
    db_session.commit()
    return teacher


# --- C-01 -------------------------------------------------------------------

def test_create_school_order_rejects_caller_without_a_centre():
    org_admin = Teacher(id=1, name="Org", role="org_admin", centre_id=None)
    with pytest.raises(HTTPException) as exc_info:
        create_school_order(SchoolCreateOrderRequest(amount=100), teacher=org_admin)
    assert exc_info.value.status_code == 400


# --- C-05 -------------------------------------------------------------------

def test_create_student_is_idempotent_on_phone_within_a_centre(db_session):
    centre = _make_centre(db_session)
    teacher = _make_teacher(db_session, centre.id)
    body = StudentCreateRequest(name="Asha", phone="98765 43210")

    created = create_student(body, db=db_session, teacher=teacher)
    assert created["phone"] == "919876543210"

    with pytest.raises(HTTPException) as exc_info:
        create_student(StudentCreateRequest(name="Asha again", phone="+91 9876543210"), db=db_session, teacher=teacher)
    assert exc_info.value.status_code == 409

    credit_events = db_session.query(CreditEvent).filter(CreditEvent.student_id == created["id"]).count()
    assert credit_events == 1
    assert db_session.query(Student).filter(Student.phone == "919876543210").count() == 1
    assert db_session.query(AuditLog).filter(AuditLog.action == "create_student").count() == 1


# --- C-03 -------------------------------------------------------------------

def test_update_student_phone_rejects_cross_centre_clash(db_session):
    centre_a = _make_centre(db_session, "School A")
    centre_b = _make_centre(db_session, "School B")
    admin_a = _make_teacher(db_session, centre_a.id)
    student_a = Student(name="A", phone="919000000001", centre_id=centre_a.id)
    student_b = Student(name="B", phone="919000000002", centre_id=centre_b.id)
    db_session.add_all([student_a, student_b])
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        update_student(student_a.id, StudentUpdateRequest(phone="9000000002"), db=db_session, teacher=admin_a)
    assert exc_info.value.status_code == 409


def test_update_student_phone_is_admin_only(db_session):
    centre = _make_centre(db_session)
    teacher = _make_teacher(db_session, centre.id, role="teacher")
    student = Student(name="A", phone="919000000001", centre_id=centre.id)
    db_session.add(student)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        update_student(student.id, StudentUpdateRequest(phone="9000000009"), db=db_session, teacher=teacher)
    assert exc_info.value.status_code == 403

    # Other fields are still editable by a teacher.
    result = update_student(student.id, StudentUpdateRequest(name="Renamed"), db=db_session, teacher=teacher)
    assert result["name"] == "Renamed"


# --- C-12 -------------------------------------------------------------------

class _FakeRazorpay:
    def __init__(self, order):
        self.utility = SimpleNamespace(verify_payment_signature=lambda *_: None)
        self.order = SimpleNamespace(fetch=lambda _id: order)
        self.payment = SimpleNamespace(fetch=lambda _id: {"status": "captured", "order_id": order["id"]})


def _raise_integrity(*_args, **_kwargs):
    raise IntegrityError("INSERT credit_events", {}, Exception("duplicate external_ref"))


def test_verify_payment_returns_idempotent_response_on_integrity_error(db_session, monkeypatch):
    student = Student(name="S", phone="919000000001")
    db_session.add(student)
    db_session.commit()
    order = {"id": "order_1", "amount": 5000, "status": "paid", "currency": "INR",
             "notes": {"student_id": str(student.id), "phone": student.phone}}
    monkeypatch.setattr(payments, "_client", _FakeRazorpay(order))
    monkeypatch.setattr(payments.cost_tracker, "add_credits", _raise_integrity)

    body = payments.VerifyPaymentRequest(
        razorpay_order_id="order_1", razorpay_payment_id="pay_1", razorpay_signature="sig", phone=student.phone,
    )
    result = payments.verify_payment(body, db=db_session)
    assert result == {"credited": 0.0, "balance": 0.0}


def test_verify_subscription_leaves_plan_untouched_on_integrity_error(db_session, monkeypatch):
    student = Student(name="S", phone="919000000001")
    db_session.add(student)
    db_session.commit()
    monkeypatch.setattr(payments.settings, "razorpay_student_plan_id", "plan_1")
    fake = SimpleNamespace(
        utility=SimpleNamespace(verify_subscription_payment_signature=lambda *_: None),
        payment=SimpleNamespace(fetch=lambda _id: {"status": "captured", "subscription_id": "sub_1"}),
    )
    monkeypatch.setattr(payments, "_client", fake)
    monkeypatch.setattr(payments.razorpay_client, "fetch_subscription", lambda _id: {
        "id": "sub_1", "status": "active", "plan_id": "plan_1",
        "notes": {"student_id": str(student.id), "phone": student.phone, "kind": "student_annual"},
    })
    monkeypatch.setattr(payments.cost_tracker, "add_credits", _raise_integrity)

    body = payments.VerifySubscriptionRequest(
        razorpay_subscription_id="sub_1", razorpay_payment_id="pay_1", razorpay_signature="sig", phone=student.phone,
    )
    result = payments.verify_student_subscription(body, db=db_session)
    assert result == {"subscription_plan": "credits", "subscription_expires_at": None}
    db_session.refresh(student)
    assert student.subscription_plan == "credits"
    assert student.razorpay_subscription_id is None


# --- C-09 -------------------------------------------------------------------

def test_period_start_uses_ist_day_boundary():
    # 01:00 IST on 10 Sept == 19:30 UTC on 9 Sept.
    now = datetime(2026, 9, 9, 19, 30, tzinfo=timezone.utc)
    start = cost_tracker._period_start("day", now=now)
    assert start == datetime(2026, 9, 9, 18, 30, tzinfo=timezone.utc)  # 00:00 IST, 10 Sept
    assert now >= start
    # 23:30 IST on 9 Sept is "yesterday" even though it's still 9 Sept in UTC.
    assert datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc) < start


# --- C-07 -------------------------------------------------------------------

def _request(host, real_ip=None):
    headers = {"x-real-ip": real_ip} if real_ip else {}
    return SimpleNamespace(client=SimpleNamespace(host=host) if host else None, headers=headers)


def test_client_ip_ignores_x_real_ip_from_a_remote_peer():
    assert rate_limit.client_ip(_request("203.0.113.5", real_ip="10.0.0.1")) == "203.0.113.5"


def test_client_ip_trusts_x_real_ip_only_behind_loopback_proxy():
    assert rate_limit.client_ip(_request("127.0.0.1", real_ip="203.0.113.9")) == "203.0.113.9"
    assert rate_limit.client_ip(_request("127.0.0.1")) == "127.0.0.1"


# --- C-02 -------------------------------------------------------------------

def test_webhook_auth_fails_closed_without_secret_outside_development(monkeypatch):
    from app.services import whatsapp_client
    monkeypatch.setattr(whatsapp_client.settings, "wati_webhook_secret", None)
    monkeypatch.setattr(whatsapp_client.settings, "environment", "production")
    assert verify_webhook_auth("Bearer anything") is False
    monkeypatch.setattr(whatsapp_client.settings, "environment", "development")
    assert verify_webhook_auth(None) is True


# --- C-04 -------------------------------------------------------------------

async def test_login_limiter_counts_failures_per_phone_and_ip(monkeypatch):
    monkeypatch.setattr(rate_limit, "_redis", None)  # in-process fallback — same logic, no Redis needed
    phone = f"8{uuid.uuid4().int % 10**9:09d}"
    assert await rate_limit.is_login_blocked(phone, "1.1.1.1") is False
    for _ in range(rate_limit.LOGIN_FAIL_MAX_PER_IP):
        await rate_limit.record_login_failure(phone, "1.1.1.1")
    assert await rate_limit.is_login_blocked(phone, "1.1.1.1") is True
    # A different IP has its own budget until the per-phone ceiling.
    assert await rate_limit.is_login_blocked(phone, "2.2.2.2") is False
