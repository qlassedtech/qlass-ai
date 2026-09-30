"""
POST /whatsapp/webhook accepts WATI_WEBHOOK_SECRET either as the
Authorization header or as a `?secret=` query parameter.

This was header-only for a short window (audit H1/H2: a query string lands
in the reverse proxy's access log). That broke every real webhook delivery
in production for ~2 weeks — confirmed live, 2026-09-30, that Wati's own
Webhook-settings UI has no field to attach a custom header on this
account's plan, so a header-only credential can never actually be sent
back to us. Query-param support is restored; the original leak is now
handled at the nginx layer instead (skoolgpt_noquery log format — see
scripts/server_hardening.sh — logs $uri, never the query string).

A rejection is an empty 403 with a once-a-minute warning, never
per-request noise.
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


def test_correct_query_secret_alone_is_accepted(webhook_client):
    resp = webhook_client.post(f"/whatsapp/webhook?secret={SECRET}", json=_payload())
    assert resp.status_code == 200
    assert resp.json()["received"] is True


def test_header_wins_when_both_are_present_and_disagree(webhook_client):
    # An expired/rotated query secret shouldn't authenticate if a correct
    # header is also present — verify_webhook_auth prefers the header.
    resp = webhook_client.post(
        f"/whatsapp/webhook?secret=stale", json=_payload(), headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert resp.status_code == 200


def test_missing_or_wrong_credential_is_an_empty_403(webhook_client):
    missing = webhook_client.post("/whatsapp/webhook", json=_payload())
    assert missing.status_code == 403
    assert missing.content == b""

    wrong_header = webhook_client.post("/whatsapp/webhook", json=_payload(), headers={"Authorization": "Bearer nope"})
    assert wrong_header.status_code == 403
    assert wrong_header.content == b""

    wrong_query = webhook_client.post("/whatsapp/webhook?secret=nope", json=_payload())
    assert wrong_query.status_code == 403
    assert wrong_query.content == b""


def test_rejections_are_logged_at_most_once_a_minute(webhook_client, caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.whatsapp_client"):
        for _ in range(3):
            webhook_client.post("/whatsapp/webhook", json=_payload(), headers={"Authorization": "Bearer nope"})
    rejections = [r for r in caplog.records if "webhook call rejected" in r.getMessage()]
    assert len(rejections) == 1
    assert "nope" not in rejections[0].getMessage()
    assert "no valid credential" in rejections[0].getMessage()
