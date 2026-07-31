import pytest

from app.services.razorpay_client import require_active_subscription, require_paid_order


def test_paid_order_requires_captured_payment_and_matching_student_notes():
    order = {
        "id": "order_1", "status": "paid", "currency": "INR",
        "notes": {"student_id": "7", "phone": "919000000007"},
    }
    payment = {"status": "captured", "order_id": "order_1"}

    require_paid_order(order, payment, {"student_id": "7", "phone": "919000000007"})

    with pytest.raises(ValueError, match="belong"):
        require_paid_order(order, payment, {"student_id": "8", "phone": "919000000008"})


def test_paid_order_rejects_an_authorized_or_wrong_order_payment():
    order = {"id": "order_1", "status": "paid", "currency": "INR", "notes": {"student_id": "7"}}

    with pytest.raises(ValueError, match="captured"):
        require_paid_order(order, {"status": "authorized", "order_id": "order_1"}, {"student_id": "7"})
    with pytest.raises(ValueError, match="captured"):
        require_paid_order(order, {"status": "captured", "order_id": "order_other"}, {"student_id": "7"})


def test_subscription_requires_its_owner_plan_and_captured_payment():
    subscription = {
        "id": "sub_1", "status": "active", "plan_id": "plan_student",
        "notes": {"student_id": "7", "phone": "919000000007", "kind": "student_annual"},
    }
    payment = {"status": "captured", "subscription_id": "sub_1"}
    expected = {"student_id": "7", "phone": "919000000007", "kind": "student_annual"}

    require_active_subscription(subscription, payment, expected, "plan_student")

    with pytest.raises(ValueError, match="unexpected plan"):
        require_active_subscription(subscription, payment, expected, "plan_teacher")
    with pytest.raises(ValueError, match="captured"):
        require_active_subscription(subscription, {"status": "captured", "subscription_id": "sub_other"}, expected, "plan_student")
