from app.routers.whatsapp import MENU_BUTTON_TO_COMMAND, MENU_BUTTONS_BASE


def test_every_base_menu_button_has_a_command_mapping():
    for button in MENU_BUTTONS_BASE:
        assert button in MENU_BUTTON_TO_COMMAND


def test_talk_to_teacher_is_not_a_proactive_menu_option():
    """
    Explicitly asserts the product decision: the AI tutor shouldn't offer a
    human-escalation button on its own default menu (undercuts its own
    credibility) — that path stays reachable only via an explicit typed
    request or the automatic hint-streak escalation, never a tappable
    button a brand-new student sees immediately.
    """
    assert "🆘 Talk to Teacher" not in MENU_BUTTONS_BASE
    assert "🆘 Talk to Teacher" not in MENU_BUTTON_TO_COMMAND
