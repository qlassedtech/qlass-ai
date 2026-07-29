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
