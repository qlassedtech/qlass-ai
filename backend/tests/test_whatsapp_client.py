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
