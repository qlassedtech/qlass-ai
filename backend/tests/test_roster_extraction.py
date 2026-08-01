import asyncio

from app.services.llm_client import LLMResult
from app.services.roster_extraction import extract_student_rows, extract_teacher_rows


def test_extract_student_rows_parses_a_clean_json_array(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model="claude-sonnet-4-6"):
        return LLMResult(
            text='[{"name": "Aman Kumar", "phone": "919000000001", "class": "10", "board": "BSEB", "school": null}]',
            model=model, input_tokens=100, output_tokens=20,
        )

    monkeypatch.setattr("app.services.roster_extraction.call_llm", fake_call_llm)

    rows, result = asyncio.run(extract_student_rows("some ocr'd roster text"))

    assert rows == [{"name": "Aman Kumar", "phone": "919000000001", "class": "10", "board": "BSEB", "school": None}]
    assert result.input_tokens == 100


def test_extract_teacher_rows_parses_a_clean_json_array(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model="claude-sonnet-4-6"):
        return LLMResult(text='[{"name": "Sunita Devi", "phone": "919000000002", "role": "admin"}]', model=model)

    monkeypatch.setattr("app.services.roster_extraction.call_llm", fake_call_llm)

    rows, _ = asyncio.run(extract_teacher_rows("some ocr'd staff list text"))

    assert rows == [{"name": "Sunita Devi", "phone": "919000000002", "role": "admin"}]


def test_extract_rows_returns_empty_list_on_malformed_json(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model="claude-sonnet-4-6"):
        return LLMResult(text="Sorry, I couldn't parse that roster.", model=model)

    monkeypatch.setattr("app.services.roster_extraction.call_llm", fake_call_llm)

    rows, _ = asyncio.run(extract_student_rows("garbled ocr text"))

    assert rows == []


def test_extract_rows_returns_empty_list_when_model_returns_a_json_object_not_array(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model="claude-sonnet-4-6"):
        return LLMResult(text='{"name": "Aman Kumar"}', model=model)

    monkeypatch.setattr("app.services.roster_extraction.call_llm", fake_call_llm)

    rows, _ = asyncio.run(extract_student_rows("some text"))

    assert rows == []
