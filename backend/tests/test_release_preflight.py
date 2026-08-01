import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from release_preflight import check  # noqa: E402

_VALID = {
    "ENVIRONMENT": "production",
    "SECRET_KEY": "a" * 40,
    "DATABASE_URL": "postgresql://qlass:realpassword@postgres:5432/qlass_ai",
    "REDIS_URL": "redis://redis:6379/0",
    "ANTHROPIC_API_KEY": "sk-ant-real",
    "WHATSAPP_TOKEN": "token",
    "WATI_API_ENDPOINT": "https://live-mt-server.wati.io/tenant",
    "PORTAL_BASE_URL": "https://portal.example.com",
    "ALLOWED_ORIGINS": "https://portal.example.com",
    "RAZORPAY_KEY_ID": "rzp_live_x",
    "RAZORPAY_KEY_SECRET": "secret",
    "POSTGRES_USER": "qlass",
    "POSTGRES_PASSWORD": "realpassword",
    "POSTGRES_DB": "qlass_ai",
}


def test_a_fully_valid_config_passes():
    assert check(dict(_VALID)) == []


def test_missing_keys_are_reported():
    values = dict(_VALID)
    del values["ANTHROPIC_API_KEY"]
    failures = check(values)
    assert any("ANTHROPIC_API_KEY" in f for f in failures)


def test_development_environment_fails():
    values = dict(_VALID)
    values["ENVIRONMENT"] = "development"
    assert any("ENVIRONMENT" in f for f in check(values))


def test_short_or_default_secret_key_fails():
    values = dict(_VALID)
    values["SECRET_KEY"] = "changeme"
    assert any("SECRET_KEY" in f for f in check(values))


def test_non_https_portal_url_fails():
    values = dict(_VALID)
    values["PORTAL_BASE_URL"] = "http://localhost:5173"
    assert any("PORTAL_BASE_URL" in f for f in check(values))


def test_wildcard_allowed_origins_fails():
    values = dict(_VALID)
    values["ALLOWED_ORIGINS"] = "*"
    assert any("ALLOWED_ORIGINS" in f for f in check(values))


def test_mismatched_postgres_credentials_fail():
    values = dict(_VALID)
    values["DATABASE_URL"] = "postgresql://someoneelse:realpassword@postgres:5432/other_db"
    failures = check(values)
    assert any("POSTGRES_USER" in f for f in failures)
    assert any("POSTGRES_DB" in f for f in failures)


def test_leftover_template_placeholder_fails():
    values = dict(_VALID)
    values["POSTGRES_PASSWORD"] = "CHANGE_ME_SAME_PASSWORD_AS_DATABASE_URL_BELOW"
    values["DATABASE_URL"] = "postgresql://qlass:CHANGE_ME_SAME_PASSWORD_AS_DATABASE_URL_BELOW@postgres:5432/qlass_ai"
    assert any("placeholder" in f for f in check(values))
