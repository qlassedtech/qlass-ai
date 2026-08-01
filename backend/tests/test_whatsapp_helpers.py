from app.routers.whatsapp import MENU_BUTTON_TO_COMMAND, MENU_BUTTONS


def test_every_menu_button_has_a_command_mapping():
    for button in MENU_BUTTONS:
        assert button in MENU_BUTTON_TO_COMMAND
