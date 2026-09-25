"""
POST /whatsapp/webhook is authenticated on the Authorization header ONLY
(audit H1/H2): the old `?secret=` query-parameter alternative wrote the
secret into the reverse proxy's access log on every delivery. A rejection
is an empty 403 with a once-a-minute warning, never per-request noise.
"""
import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import whatsapp
from app.services import whatsapp_client

SECRET = "w" * 40


@pytest.fixture()
def webhook_client(db_session, monkeypatch):
    monkeypatch.setattr(whatsapp_client.settings, "wati_webhook_secret", SECRET)
    monkeypatch.setattr(whatsapp_client.settings, "environment", "production")
    # The handler persists the job through its own SessionLocal() (not the
    # get_db dependency) and then spawns the tutor pipeline — point the
    # former at the SQLite test session and swallow the latter.
    monkeypatch.setattr(whatsapp, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(whatsapp, "_spawn", lambda coro: coro.close())
    monkeypatch.setattr(whatsapp_client, "_last_auth_reject_log_at", 0.0)
    return TestClient(app)


def _payload() -> dict:
    return {"whatsappMessageId": str(uuid.uuid4()), "waId": "919000000001", "text": "hi", "type": "text"}


def test_correct_header_is_accepted(webhook_client):
    resp = webhook_client.post("/whatsapp/webhook", json=_payload(), headers={"Authorization": f"Bearer {SECRET}"})
    assert resp.status_code == 200
    assert resp.json()["received"] is True
    # Wati's dashboard sends the raw value too — with no "Bearer " prefix.
    resp = webhook_client.post("/whatsapp/webhook", json=_payload(), headers={"Authorization": SECRET})
    assert resp.status_code == 200


def test_missing_or_wrong_header_is_an_empty_403(webhook_client):
    missing = webhook_client.post("/whatsapp/webhook", json=_payload())
    assert missing.status_code == 403
    assert missing.content == b""

    wrong = webhook_client.post("/whatsapp/webhook", json=_payload(), headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 403
    assert wrong.content == b""


def test_query_param_secret_alone_is_rejected(webhook_client):
    resp = webhook_client.post(f"/whatsapp/webhook?secret={SECRET}", json=_payload())
    assert resp.status_code == 403
    assert resp.content == b""


def test_rejections_are_logged_at_most_once_a_minute(webhook_client, caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.whatsapp_client"):
        for _ in range(3):
            webhook_client.post("/whatsapp/webhook", json=_payload(), headers={"Authorization": "Bearer nope"})
    rejections = [r for r in caplog.records if "webhook call rejected" in r.getMessage()]
    assert len(rejections) == 1
    assert "nope" not in rejections[0].getMessage()
    assert "did not match" in rejections[0].getMessage()


def test_verify_webhook_auth_no_longer_takes_a_query_secret():
    with pytest.raises(TypeError):
        whatsapp_client.verify_webhook_auth(None, SECRET)  # type: ignore[call-arg]
