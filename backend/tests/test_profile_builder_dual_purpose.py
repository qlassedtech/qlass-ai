from app.services.profile_builder import extract_profile_answer


def test_extracts_trailing_name_without_discarding_the_academic_content_before_it():
    # Reproduces a real conversation: the tutor asked "are you comfortable
    # with basic differentiation and integration?" and appended "what's
    # your name?" to the same reply. The student answered both at once.
    result = extract_profile_answer("name", "I don't know. Nikhil")
    assert result is not None
    value, remaining = result
    assert value == "Nikhil"
    assert remaining == "I don't know."


def test_pure_answer_with_nothing_else_has_empty_remainder():
    result = extract_profile_answer("name", "Nikhil")
    assert result == ("Nikhil", "")


def test_returns_none_when_no_segment_looks_like_a_valid_answer():
    assert extract_profile_answer("name", "What do you mean?") is None
    assert extract_profile_answer("class_", "I'm not sure what my class is") is None


def test_extracts_trailing_class_number_from_a_longer_message():
    result = extract_profile_answer("class_", "Not sure honestly. I think 9")
    assert result is not None
    value, remaining = result
    assert value == "9"
    assert remaining == "Not sure honestly."
