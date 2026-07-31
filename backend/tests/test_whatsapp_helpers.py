from app.routers.whatsapp import MENU_BUTTON_TO_COMMAND, _looks_affirmative


def test_plain_yes_is_affirmative():
    assert _looks_affirmative("yes") is True
    assert _looks_affirmative("sure sounds good") is True


def test_plain_no_is_not_affirmative():
    assert _looks_affirmative("no") is False
    assert _looks_affirmative("No thanks") is False


def test_no_wins_even_with_incidental_please():
    """
    Regression test for a real production bug: "No please provide me quiz
    on the same" was misread as affirmative because "please" is itself in
    _AFFIRMATIVE_WORDS and the old check never looked for a negative word.
    """
    assert _looks_affirmative("No please provide me quiz on the same") is False


def test_dont_is_negative():
    assert _looks_affirmative("don't update it") is False


def test_every_menu_button_has_a_command_mapping():
    from app.routers.whatsapp import MENU_BUTTONS

    for button in MENU_BUTTONS:
        assert button in MENU_BUTTON_TO_COMMAND
