from app.services.whatsapp_client import parse_incoming_button_reply


def test_parse_button_reply_from_interactive_button_reply_shape():
    payload = {"owner": False, "waId": "919000000001", "interactiveButtonReply": {"title": "📊 My Progress"}}
    assert parse_incoming_button_reply(payload) == ("919000000001", "📊 My Progress")


def test_parse_button_reply_from_list_reply_shape():
    payload = {"owner": False, "waId": "919000000001", "listReply": {"title": "🎁 Refer a Friend"}}
    assert parse_incoming_button_reply(payload) == ("919000000001", "🎁 Refer a Friend")


def test_parse_button_reply_ignores_outgoing_echo():
    payload = {"owner": True, "waId": "919000000001", "interactiveButtonReply": {"title": "📊 My Progress"}}
    assert parse_incoming_button_reply(payload) is None


def test_parse_button_reply_returns_none_for_plain_text_message():
    payload = {"owner": False, "waId": "919000000001", "type": "text", "text": "hello"}
    assert parse_incoming_button_reply(payload) is None


def test_parse_button_reply_from_template_quick_reply_button_shape():
    """
    A tap on a Quick Reply button attached to an approved template (see
    app.services.nudges' "Know More" button) is a different message
    component from our own interactive send — Wati reports it with
    type="button" rather than type="interactive", per Wati's webhook docs.
    """
    payload = {"owner": False, "waId": "919000000001", "type": "button", "text": "Know More", "button": {"text": "Know More", "payload": "know-more"}}
    assert parse_incoming_button_reply(payload) == ("919000000001", "Know More")


def test_parse_button_reply_matches_real_confirmed_wati_payload():
    """
    The exact real payload Wati sent for a live button tap on 2026-07-29
    (waId/message ids redacted) — locks in the confirmed shape as a
    regression test now that it's no longer just a best-effort guess.
    """
    payload = {
        "type": "interactive",
        "text": "📊 My Progress",
        "owner": False,
        "waId": "918460184666",
        "listReply": None,
        "interactiveButtonReply": {"id": "1", "title": "📊 My Progress"},
        "buttonReply": None,
    }
    assert parse_incoming_button_reply(payload) == ("918460184666", "📊 My Progress")


import pytest

from app.services import whatsapp_client
from app.services.whatsapp_client import _tpl, send_notification


def test_tpl_collapses_whitespace_and_newlines():
    assert _tpl("  Hello\n\n  world \t again\r\n") == "Hello world again"


def test_tpl_truncates_to_900_chars():
    assert len(_tpl("x" * 2000)) == 900


def test_tpl_coerces_non_strings():
    assert _tpl(42) == "42"


@pytest.mark.asyncio
async def test_send_notification_uses_template_when_configured(monkeypatch):
    calls = {}

    async def fake_template(to_phone, template_name, params):
        calls["template"] = (to_phone, template_name, params)
        return {"sent": True}

    async def fake_session(to_phone, body):
        calls["session"] = (to_phone, body)
        return {"sent": True}

    monkeypatch.setattr(whatsapp_client, "send_template_message", fake_template)
    monkeypatch.setattr(whatsapp_client, "send_whatsapp_message", fake_session)
    result = await send_notification("919000000001", "parent_digest", ["Asha", "Ravi", "5 msgs\nthis week"], "fallback")
    assert result == {"sent": True}
    assert "session" not in calls
    assert calls["template"] == (
        "919000000001", "parent_digest",
        [{"name": "1", "value": "Asha"}, {"name": "2", "value": "Ravi"}, {"name": "3", "value": "5 msgs this week"}],
    )


@pytest.mark.asyncio
async def test_send_notification_falls_back_to_session_message_without_template(monkeypatch):
    calls = {}

    async def fake_template(to_phone, template_name, params):
        calls["template"] = True
        return {"sent": True}

    async def fake_session(to_phone, body):
        calls["session"] = (to_phone, body)
        return {"sent": True}

    monkeypatch.setattr(whatsapp_client, "send_template_message", fake_template)
    monkeypatch.setattr(whatsapp_client, "send_whatsapp_message", fake_session)
    await send_notification("919000000001", None, ["Asha"], "full multi-line\nfallback")
    assert "template" not in calls
    assert calls["session"] == ("919000000001", "full multi-line\nfallback")
