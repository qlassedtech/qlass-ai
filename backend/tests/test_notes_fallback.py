"""chat_core._generate_notes must not wrap the LLM outage apology up as study notes (audit Sept 2026, P2)."""
import pytest

from app.models.core import Centre, ChatHistory, Student
from app.services import chat_core, llm_client
from app.services.llm_client import LLMResult


def _student_with_history(db_session) -> Student:
    centre = Centre(name="Notes School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Notes Student", phone="919000000301", centre_id=centre.id)
    db_session.add(student)
    db_session.commit()
    db_session.add(ChatHistory(student_id=student.id, role="user", message="what is osmosis", agent="tutor"))
    db_session.add(ChatHistory(student_id=student.id, role="assistant", message="Osmosis is...", agent="tutor"))
    db_session.commit()
    return student


@pytest.mark.parametrize(
    "result",
    [
        LLMResult(text="Sorry, I'm having trouble reaching the AI service right now. Please try again in a bit.",
                  model="claude-haiku-4-5-20251001", ok=False),
        LLMResult(text="Sorry, I'm having trouble reaching the AI service right now.", model="claude-haiku-4-5-20251001"),
    ],
    ids=["ok-flag-false", "apology-text-without-flag"],
)
async def test_llm_failure_returns_a_short_try_again_message(db_session, monkeypatch, result):
    student = _student_with_history(db_session)

    async def fake_call_llm(system_prompt, messages, model="x"):
        return result

    monkeypatch.setattr(llm_client, "call_llm", fake_call_llm)

    reply = await chat_core._generate_notes(db_session, student)

    assert reply == chat_core.NOTES_UNAVAILABLE_REPLY
    assert "having trouble" not in reply
    assert "📝" not in reply


async def test_successful_notes_are_still_formatted_as_notes(db_session, monkeypatch):
    student = _student_with_history(db_session)

    async def fake_call_llm(system_prompt, messages, model="x"):
        return LLMResult(text="- *Osmosis*: water moves across a membrane", model="claude-haiku-4-5-20251001",
                         input_tokens=10, output_tokens=5)

    monkeypatch.setattr(llm_client, "call_llm", fake_call_llm)

    reply = await chat_core._generate_notes(db_session, student)

    assert reply.startswith("📝 *Notes on what we've covered:*")
    assert "Osmosis" in reply
