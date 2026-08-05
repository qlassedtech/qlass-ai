from app.services.phone import normalize_phone


def test_normalize_phone_adds_country_code_to_bare_ten_digits():
    assert normalize_phone("8460184666") == "918460184666"
    assert normalize_phone("91-8460-184-666") == "918460184666"
    assert normalize_phone("+918460184666") == "918460184666"


def test_normalize_phone_leaves_already_prefixed_number_unchanged():
    assert normalize_phone("919031003985") == "919031003985"
