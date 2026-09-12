from app.agents.tutor_agent import TutorAgent
from app.models.core import Centre, Student


def _make_student(db_session):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Test Student", phone="919000000002", centre_id=centre.id, class_="8")
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def test_student_tutor_style_defaults_to_balanced(db_session):
    # Every existing student must stay on today's unchanged behavior — a
    # new column defaulting to anything else would silently change
    # behavior for every student the moment this migration ran.
    student = _make_student(db_session)
    assert student.tutor_style == "balanced"


def test_build_context_hint_first_rule_only_appears_when_selected():
    agent = TutorAgent()
    profile = {"class": "8", "board": "CBSE"}

    balanced_static, _ = agent.build_context(profile, [], [], tutor_style="balanced")
    hint_first_static, _ = agent.build_context(profile, [], [], tutor_style="hint_first")

    assert "HINT MODE turned on" not in balanced_static
    assert "HINT MODE turned on" in hint_first_static
    # The default softer rule must still be there for a balanced student —
    # switching one student to hint_first must never remove the other
    # style's own instructions from a balanced student's prompt.
    assert "do NOT immediately solve the whole thing" in balanced_static
    assert "do NOT immediately solve the whole thing" not in hint_first_static


def test_build_context_defaults_to_balanced_when_tutor_style_omitted():
    # Every existing caller not yet passing tutor_style must keep getting
    # today's unchanged behavior.
    agent = TutorAgent()
    static_prompt, _ = agent.build_context({"class": "8", "board": "CBSE"}, [], [])
    assert "HINT MODE turned on" not in static_prompt
