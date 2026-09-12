"""
Covers the voice-call WebSocket endpoint (app.routers.voice_call) — the
first WebSocket code in this codebase, so there's no existing test pattern
to mirror; this uses FastAPI's own TestClient.websocket_connect, with the
app's get_db dependency overridden to the same in-memory SQLite db_session
fixture every other test in this file uses (see conftest.py), so nothing
here ever touches the real dev/prod database configured in .env.
"""
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.database import get_db
from app.main import app
from app.models.core import Centre, Student
from app.services import chat_core, cost_tracker, sarvam_client, sketch_client
from app.services.chat_core import ChatTurnResult
from app.services.llm_client import LLMResult
from app.services.student_auth import create_student_access_token


def _make_student(db_session, *, voice_enabled: bool = True) -> Student:
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(
        name="Test Student", phone="919000000002", centre_id=centre.id, class_="8",
        features={"voice": voice_enabled, "ocr": False, "documents": False, "youtube_videos": False},
    )
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def _client(db_session) -> TestClient:
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def test_connect_without_token_is_rejected(db_session):
    client = _client(db_session)
    try:
        with client.websocket_connect("/ws/voice-call") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error"
            # The server closes right after — trying to read again surfaces
            # the disconnect rather than hanging.
            import pytest as _pytest
            with _pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    finally:
        app.dependency_overrides.clear()


def test_connect_with_garbage_token_is_rejected(db_session):
    client = _client(db_session)
    try:
        with client.websocket_connect("/ws/voice-call?token=not-a-real-token") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error"
    finally:
        app.dependency_overrides.clear()


def test_connect_without_voice_feature_is_rejected(db_session):
    student = _make_student(db_session, voice_enabled=False)
    token = create_student_access_token(student.id)
    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?token={token}") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert "voice" in frame["message"].lower()
    finally:
        app.dependency_overrides.clear()


def test_happy_path_turn_transcribes_replies_and_synthesizes_speech(db_session, monkeypatch):
    """
    Full turn with Sarvam and chat_core mocked out — mirrors the mocking
    style test_student_chat.py already uses for chat_core internals
    (monkeypatch.setattr on the module object, not the imported name), so
    voice_call.py's own module-level references to sarvam_client.* and
    chat_core.process_message pick up the fakes.
    """
    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    token = create_student_access_token(student.id)

    async def fake_transcribe(audio_bytes, filename="voice_note.ogg"):
        return "what is photosynthesis"

    async def fake_process_message(db, student, message_text):
        assert message_text == "what is photosynthesis"
        return ChatTurnResult(reply_text="Photosynthesis is how plants make food from sunlight.")

    async def fake_synthesize(text, language_code=None, speaker=None):
        return b"fake-opus-bytes"

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(chat_core, "process_message", fake_process_message)
    monkeypatch.setattr(sarvam_client, "synthesize_speech", fake_synthesize)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?token={token}") as ws:
            ws.send_bytes(b"\x00\x01\x02fake-audio-bytes")

            transcript_frame = ws.receive_json()
            assert transcript_frame == {"type": "transcript", "text": "what is photosynthesis"}

            reply_frame = ws.receive_json()
            assert reply_frame["type"] == "reply_text"
            assert "Photosynthesis" in reply_frame["text"]

            audio_frame = ws.receive_bytes()
            assert audio_frame == b"fake-opus-bytes"
    finally:
        app.dependency_overrides.clear()


def test_failed_transcription_sends_error_but_keeps_connection_open(db_session, monkeypatch):
    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    token = create_student_access_token(student.id)

    async def fake_transcribe_fails(audio_bytes, filename="voice_note.ogg"):
        return None

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fake_transcribe_fails)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?token={token}") as ws:
            ws.send_bytes(b"garbled")
            frame = ws.receive_json()
            assert frame["type"] == "error"

            # Connection is still open for a second attempt — send another
            # turn and confirm it's still being processed, not disconnected.
            ws.send_bytes(b"garbled-again")
            frame2 = ws.receive_json()
            assert frame2["type"] == "error"
    finally:
        app.dependency_overrides.clear()


def test_image_prompt_reply_sends_diagram_frame_before_reply_text(db_session, monkeypatch):
    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    token = create_student_access_token(student.id)

    scene = [{"type": "rect", "x": 10, "y": 10, "w": 50, "h": 50}]

    async def fake_transcribe(audio_bytes, filename="voice_note.ogg"):
        return "draw a plant cell"

    async def fake_process_message(db, student, message_text):
        return ChatTurnResult(reply_text="Here's the plant cell.", image_prompt="a plant cell")

    async def fake_generate_sketch_scene(prompt):
        assert prompt == "a plant cell"
        return scene, LLMResult(text="...", model="claude-haiku-4-5-20251001", input_tokens=5, output_tokens=5)

    async def fake_synthesize(text, language_code=None, speaker=None):
        return b"fake-opus-bytes"

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(chat_core, "process_message", fake_process_message)
    monkeypatch.setattr(sketch_client, "generate_sketch_scene", fake_generate_sketch_scene)
    monkeypatch.setattr(sarvam_client, "synthesize_speech", fake_synthesize)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?token={token}") as ws:
            ws.send_bytes(b"some-audio")
            ws.receive_json()  # transcript

            diagram_frame = ws.receive_json()
            assert diagram_frame == {"type": "diagram", "scene": scene}

            reply_frame = ws.receive_json()
            assert reply_frame["type"] == "reply_text"
    finally:
        app.dependency_overrides.clear()


def test_failed_sketch_generation_does_not_send_diagram_or_fail_the_turn(db_session, monkeypatch):
    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    token = create_student_access_token(student.id)

    async def fake_transcribe(audio_bytes, filename="voice_note.ogg"):
        return "draw a plant cell"

    async def fake_process_message(db, student, message_text):
        return ChatTurnResult(reply_text="Here's the plant cell.", image_prompt="a plant cell")

    async def fake_generate_sketch_scene_fails(prompt):
        return None, None

    async def fake_synthesize(text, language_code=None, speaker=None):
        return b"fake-opus-bytes"

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(chat_core, "process_message", fake_process_message)
    monkeypatch.setattr(sketch_client, "generate_sketch_scene", fake_generate_sketch_scene_fails)
    monkeypatch.setattr(sarvam_client, "synthesize_speech", fake_synthesize)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?token={token}") as ws:
            ws.send_bytes(b"some-audio")
            ws.receive_json()  # transcript

            # No diagram frame — straight to reply_text, turn proceeds normally.
            reply_frame = ws.receive_json()
            assert reply_frame["type"] == "reply_text"

            audio_frame = ws.receive_bytes()
            assert audio_frame == b"fake-opus-bytes"
    finally:
        app.dependency_overrides.clear()


def test_tts_failure_degrades_to_text_only(db_session, monkeypatch):
    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    token = create_student_access_token(student.id)

    async def fake_transcribe(audio_bytes, filename="voice_note.ogg"):
        return "hello"

    async def fake_process_message(db, student, message_text):
        return ChatTurnResult(reply_text="Hi there!")

    async def fake_synthesize_fails(text, language_code=None, speaker=None):
        return None

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(chat_core, "process_message", fake_process_message)
    monkeypatch.setattr(sarvam_client, "synthesize_speech", fake_synthesize_fails)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?token={token}") as ws:
            ws.send_bytes(b"some-audio")
            ws.receive_json()  # transcript
            ws.receive_json()  # reply_text
            frame = ws.receive_json()
            assert frame == {"type": "tts_failed"}
    finally:
        app.dependency_overrides.clear()
