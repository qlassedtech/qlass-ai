import pytest

from app.services.razorpay_client import require_active_subscription, require_paid_order

EXPECTED_NOTES = {"student_id": "42", "phone": "9990001111"}


def _order(**overrides):
    base = {"status": "paid", "currency": "INR", "id": "order_1", "notes": dict(EXPECTED_NOTES)}
    base.update(overrides)
    return base


def _payment(**overrides):
    base = {"status": "captured", "order_id": "order_1"}
    base.update(overrides)
    return base


def test_require_paid_order_accepts_valid_matching_order_and_payment():
    require_paid_order(_order(), _payment(), EXPECTED_NOTES)  # must not raise


def test_require_paid_order_rejects_unpaid_order():
    with pytest.raises(ValueError):
        require_paid_order(_order(status="created"), _payment(), EXPECTED_NOTES)


def test_require_paid_order_rejects_non_inr_currency():
    with pytest.raises(ValueError):
        require_paid_order(_order(currency="USD"), _payment(), EXPECTED_NOTES)


def test_require_paid_order_rejects_uncaptured_payment():
    with pytest.raises(ValueError):
        require_paid_order(_order(), _payment(status="failed"), EXPECTED_NOTES)


def test_require_paid_order_rejects_payment_for_a_different_order():
    with pytest.raises(ValueError):
        require_paid_order(_order(), _payment(order_id="order_other"), EXPECTED_NOTES)


def test_require_paid_order_rejects_order_belonging_to_another_student():
    # This is the core anti-fraud check: a valid signature alone must never
    # let one customer's payment credit a different student's wallet.
    mismatched_notes = {"student_id": "999", "phone": "9990001111"}
    with pytest.raises(ValueError):
        require_paid_order(_order(notes=mismatched_notes), _payment(), EXPECTED_NOTES)


def test_require_paid_order_rejects_missing_notes():
    with pytest.raises(ValueError):
        require_paid_order(_order(notes={}), _payment(), EXPECTED_NOTES)


def _subscription(**overrides):
    base = {"status": "active", "plan_id": "plan_1", "id": "sub_1", "notes": dict(EXPECTED_NOTES)}
    base.update(overrides)
    return base


def _sub_payment(**overrides):
    base = {"status": "captured", "subscription_id": "sub_1"}
    base.update(overrides)
    return base


def test_require_active_subscription_accepts_valid_active_subscription():
    require_active_subscription(_subscription(), _sub_payment(), EXPECTED_NOTES, "plan_1")  # must not raise


def test_require_active_subscription_accepts_authenticated_status():
    # Razorpay marks a mandate "authenticated" before the first charge
    # settles as "active" — both are valid states for confirming signup.
    require_active_subscription(_subscription(status="authenticated"), _sub_payment(), EXPECTED_NOTES, "plan_1")


def test_require_active_subscription_rejects_inactive_subscription():
    with pytest.raises(ValueError):
        require_active_subscription(_subscription(status="cancelled"), _sub_payment(), EXPECTED_NOTES, "plan_1")


def test_require_active_subscription_rejects_wrong_plan():
    with pytest.raises(ValueError):
        require_active_subscription(_subscription(), _sub_payment(), EXPECTED_NOTES, "plan_other")


def test_require_active_subscription_rejects_uncaptured_payment():
    with pytest.raises(ValueError):
        require_active_subscription(_subscription(), _sub_payment(status="pending"), EXPECTED_NOTES, "plan_1")


def test_require_active_subscription_rejects_payment_for_different_subscription():
    with pytest.raises(ValueError):
        require_active_subscription(
            _subscription(), _sub_payment(subscription_id="sub_other"), EXPECTED_NOTES, "plan_1"
        )


def test_require_active_subscription_rejects_subscription_belonging_to_another_student():
    mismatched_notes = {"student_id": "999", "phone": "9990001111"}
    with pytest.raises(ValueError):
        require_active_subscription(
            _subscription(notes=mismatched_notes), _sub_payment(), EXPECTED_NOTES, "plan_1"
        )
