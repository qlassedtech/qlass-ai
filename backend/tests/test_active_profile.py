from types import SimpleNamespace

from app.services.active_profile import (
    build_disambiguation_prompt,
    extract_switch_target_name,
    looks_like_new_profile_request,
    match_student_by_name,
)


def _student(name):
    return SimpleNamespace(name=name)


def test_looks_like_new_profile_request_matches_known_phrases():
    assert looks_like_new_profile_request("this is for my different child") is True
    assert looks_like_new_profile_request("what is photosynthesis") is False


def test_extract_switch_target_name_matches_common_patterns():
    assert extract_switch_target_name("switch to Priya") == "priya"
    assert extract_switch_target_name("it's Raj now") == "raj"
    assert extract_switch_target_name("this is Priya talking") == "priya"
    assert extract_switch_target_name("what is gravity") is None


def test_build_disambiguation_prompt_two_names():
    prompt = build_disambiguation_prompt(["Priya", "Raj"])
    assert "Priya or Raj" in prompt


def test_build_disambiguation_prompt_three_names():
    prompt = build_disambiguation_prompt(["Priya", "Raj", "Amit"])
    assert "Priya, Raj or Amit" in prompt


def test_match_student_by_name_matches_whole_word():
    students = [_student("Priya"), _student("Raj")]
    match = match_student_by_name(students, "hi it's raj, what is friction")
    assert match is not None
    assert match.name == "Raj"


def test_match_student_by_name_does_not_false_positive_on_substring():
    # Regression test: "Om" must not match just because it's a substring of
    # "tomorrow" — this previously mis-attributed an ordinary question to
    # the wrong sibling on a shared family phone.
    students = [_student("Om"), _student("Priya")]
    match = match_student_by_name(students, "what is the homework for tomorrow")
    assert match is None


def test_match_student_by_name_does_not_false_positive_on_substring_of_another_name():
    # "Ria" is a substring of "Maria" — must not match on that alone.
    students = [_student("Ria")]
    match = match_student_by_name(students, "tell me about Maria Curie's discoveries")
    assert match is None


def test_match_student_by_name_skips_placeholder_name():
    students = [_student("New Student")]
    assert match_student_by_name(students, "new student here, help me") is None


def test_match_student_by_name_returns_none_when_no_match():
    students = [_student("Priya"), _student("Raj")]
    assert match_student_by_name(students, "what is 2+2") is None
