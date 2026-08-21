"""
Bulk roster upload accepts a CSV (or a roster photo/PDF/Word file) and
previews row dicts before anything is created — see
app.routers.admin._rows_from_roster_upload and app.services.roster_extraction.
"""
import io

from fastapi import UploadFile

from app.routers.admin import _rows_from_roster_upload


def _csv_upload(text: str, filename: str = "roster.csv") -> UploadFile:
    return UploadFile(file=io.BytesIO(text.encode("utf-8")), filename=filename)


async def test_csv_with_our_own_headers_uses_the_fast_path_without_calling_the_llm(db_session):
    csv_text = "name,phone,class,board,school\nAman Kumar,919000000001,10,BSEB,Patna High School\n"

    async def fail_if_called(source_text):
        raise AssertionError("headers matching our own vocabulary must never hit the LLM extraction path")

    rows = await _rows_from_roster_upload(
        _csv_upload(csv_text), fail_if_called, db_session, None, {"name", "phone", "class", "board", "school"},
    )

    assert rows == [{"name": "Aman Kumar", "phone": "919000000001", "class": "10", "board": "BSEB", "school": "Patna High School"}]


async def test_csv_header_order_and_subset_still_use_the_fast_path(db_session):
    csv_text = "phone,name\n919000000002,Sunita Devi\n"

    async def fail_if_called(source_text):
        raise AssertionError("a subset/reordered set of our own headers must still use the fast path")

    rows = await _rows_from_roster_upload(
        _csv_upload(csv_text), fail_if_called, db_session, None, {"name", "phone", "class", "board", "school"},
    )

    assert rows == [{"phone": "919000000002", "name": "Sunita Devi"}]


async def test_csv_with_a_school_own_column_names_falls_back_to_llm_extraction(db_session, monkeypatch):
    """
    Confirmed live: a school's own exported CSV ("Student Name", "Mobile
    No", ...) used to parse "successfully" via exact-match csv.DictReader
    but produce empty name/phone on every row, silently skipping the
    whole batch. Non-matching headers must route through the same
    LLM-extraction path a roster photo already uses.
    """
    csv_text = "Student Name,Mobile No,Grade\nAman Kumar,919000000001,10\n"
    seen = {}

    class FakeLLMResult:
        input_tokens = 10
        output_tokens = 5

    async def fake_extract(source_text):
        seen["source_text"] = source_text
        return [{"name": "Aman Kumar", "phone": "919000000001", "class": "10", "board": None, "school": None}], FakeLLMResult()

    billed = []
    monkeypatch.setattr(
        "app.routers.admin.school_billing.record_claude_usage",
        lambda db, centre_id, service, in_tok, out_tok: billed.append((centre_id, service)),
    )

    rows = await _rows_from_roster_upload(
        _csv_upload(csv_text), fake_extract, db_session, 42, {"name", "phone", "class", "board", "school"},
    )

    assert rows == [{"name": "Aman Kumar", "phone": "919000000001", "class": "10", "board": None, "school": None}]
    assert "Student Name" in seen["source_text"]
    assert billed == [(42, "roster_extraction")]
