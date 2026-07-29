from app.models.core import Centre
from app.services import school_billing


def _make_centre(db_session):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    return centre


def test_school_balance_starts_at_zero(db_session):
    centre = _make_centre(db_session)
    assert school_billing.get_balance(db_session, centre.id) == 0
    assert school_billing.has_credits(db_session, centre.id) is False


def test_school_trial_credits(db_session):
    centre = _make_centre(db_session)
    balance = school_billing.add_trial_credits(db_session, centre.id)
    assert balance == school_billing.SCHOOL_TRIAL_CREDITS


def test_workbook_usage_billed_with_markup(db_session):
    centre = _make_centre(db_session)
    school_billing.add_credits(db_session, centre.id, 100.0)
    rates = school_billing.PRICING["workbook_pdf"]
    input_tokens, output_tokens = 1000, 500
    expected_raw = (input_tokens / 1000) * rates["input_per_1k_tokens"] + (output_tokens / 1000) * rates["output_per_1k_tokens"]
    balance = school_billing.record_claude_usage(db_session, centre.id, "workbook_pdf", input_tokens, output_tokens)
    assert balance == round(100.0 - expected_raw * school_billing.MARKUP_MULTIPLIER, 10)


def test_gamma_usage_billed_from_real_credits_not_flat_estimate(db_session):
    """
    Regression test for the audit fix: Gamma presentations must be billed
    from Gamma's own reported credit cost (real per-generation figure),
    not a flat guessed rate.
    """
    centre = _make_centre(db_session)
    school_billing.add_credits(db_session, centre.id, 100.0)

    # The real figure confirmed from the Qlass Gamma account: Pro plan,
    # ₹1300/4000 credits = ₹0.325/credit.
    assert school_billing.GAMMA_CREDIT_INR == 1300 / 4000

    credits_deducted = 13  # the actual cost of a real 3-slide test generation
    expected_raw = credits_deducted * school_billing.GAMMA_CREDIT_INR
    balance = school_billing.record_gamma_usage(db_session, centre.id, "gen_test_1", credits_deducted)
    assert balance == round(100.0 - expected_raw * school_billing.MARKUP_MULTIPLIER, 10)


def test_gamma_generation_never_billed_twice(db_session):
    """
    Regression test: the frontend polls /admin/presentation/status
    repeatedly until a generation completes — the same generation_id must
    only ever be billed once across those repeated polls.
    """
    centre = _make_centre(db_session)
    school_billing.add_credits(db_session, centre.id, 100.0)

    assert school_billing.has_billed_gamma_generation(db_session, "gen_abc") is False
    school_billing.record_gamma_usage(db_session, centre.id, "gen_abc", 13)
    assert school_billing.has_billed_gamma_generation(db_session, "gen_abc") is True

    balance_after_first_bill = school_billing.get_balance(db_session, centre.id)
    # A second poll for the same generation_id — the caller (admin.py) checks
    # has_billed_gamma_generation before calling record_gamma_usage again;
    # simulate that guard here.
    if not school_billing.has_billed_gamma_generation(db_session, "gen_abc"):
        school_billing.record_gamma_usage(db_session, centre.id, "gen_abc", 13)
    assert school_billing.get_balance(db_session, centre.id) == balance_after_first_bill


def test_school_payment_idempotency(db_session):
    centre = _make_centre(db_session)
    assert school_billing.has_processed_external_ref(db_session, "pay_school_1") is False
    school_billing.add_credits(db_session, centre.id, 500.0, external_ref="pay_school_1")
    assert school_billing.has_processed_external_ref(db_session, "pay_school_1") is True
    assert school_billing.get_balance(db_session, centre.id) == 500.0
