"""
Covers the voice-call WebSocket endpoint (app.routers.voice_call) — the
first WebSocket code in this codebase, so there's no existing test pattern
to mirror; this uses FastAPI's own TestClient.websocket_connect, with the
app's get_db dependency overridden to the same in-memory SQLite db_session
fixture every other test in this file uses (see conftest.py), so nothing
here ever touches the real dev/prod database configured in .env.
"""
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.database import get_db
from app.main import app
from app.models.core import Centre, Student
from app.services import chat_core, cost_tracker, sarvam_client, sketch_client
from app.services.chat_core import ChatTurnResult
from app.services.llm_client import LLMResult
from app.services.student_auth import create_student_access_token, create_voice_call_ticket


def _make_student(db_session, *, voice_enabled: bool = True, sales_status: str = "active") -> Student:
    centre = Centre(name="Test School", sales_status=sales_status)
    db_session.add(centre)
    db_session.commit()
    # Unique per test: the endpoint now applies the real per-student rate
    # limit (backed by real Redis when it's reachable), so a phone shared
    # across every test in this file would trip it partway through a run.
    student = Student(
        name="Test Student", phone=f"9{uuid.uuid4().int % 10**9:09d}", centre_id=centre.id, class_="8",
        features={"voice": voice_enabled, "ocr": False, "documents": False, "youtube_videos": False},
    )
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def _ticket(student: Student) -> str:
    """The short-lived single-use credential the WebSocket takes as ?ticket= (see student_auth)."""
    return create_voice_call_ticket(student)


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


def test_connect_with_garbage_ticket_is_rejected(db_session):
    client = _client(db_session)
    try:
        with client.websocket_connect("/ws/voice-call?ticket=not-a-real-ticket") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error"
    finally:
        app.dependency_overrides.clear()


def test_connect_without_voice_feature_is_rejected(db_session):
    student = _make_student(db_session, voice_enabled=False)
    token = _ticket(student)
    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={token}") as ws:
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
    token = _ticket(student)

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
        with client.websocket_connect(f"/ws/voice-call?ticket={token}") as ws:
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
    token = _ticket(student)

    async def fake_transcribe_fails(audio_bytes, filename="voice_note.ogg"):
        return None

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fake_transcribe_fails)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={token}") as ws:
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
    token = _ticket(student)

    scene = [{"type": "rect", "x": 10, "y": 10, "w": 50, "h": 50}]

    async def fake_transcribe(audio_bytes, filename="voice_note.ogg"):
        return "draw a plant cell"

    async def fake_process_message(db, student, message_text):
        return ChatTurnResult(reply_text="Here's the plant cell.", image_prompt="a plant cell")

    async def fake_generate_sketch_scene(prompt):
        assert prompt == "a plant cell"
        # generate_sketch_scene makes two Claude calls (generation +
        # critique) and returns each pass's usage separately so the
        # caller can bill — and tag — them individually; this router-level
        # test just mocks both passes rather than exercising the real
        # sketch_client pipeline.
        return (
            scene,
            LLMResult(text="...", model="claude-sonnet-4-6", input_tokens=5, output_tokens=5),
            LLMResult(text="OK", model="claude-haiku-4-5-20251001", input_tokens=2, output_tokens=1),
        )

    async def fake_synthesize(text, language_code=None, speaker=None):
        return b"fake-opus-bytes"

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(chat_core, "process_message", fake_process_message)
    monkeypatch.setattr(sketch_client, "generate_sketch_scene", fake_generate_sketch_scene)
    monkeypatch.setattr(sarvam_client, "synthesize_speech", fake_synthesize)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={token}") as ws:
            ws.send_bytes(b"some-audio")
            ws.receive_json()  # transcript

            diagram_frame = ws.receive_json()
            assert diagram_frame == {"type": "diagram", "scene": scene}

            reply_frame = ws.receive_json()
            assert reply_frame["type"] == "reply_text"
    finally:
        app.dependency_overrides.clear()


def test_video_reply_sends_video_frame(db_session, monkeypatch):
    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    token = _ticket(student)

    async def fake_transcribe(audio_bytes, filename="voice_note.ogg"):
        return "show me a video on photosynthesis"

    async def fake_process_message(db, student, message_text):
        return ChatTurnResult(
            reply_text="Here's a video on photosynthesis.",
            video={"title": "Photosynthesis Explained", "url": "https://youtube.com/watch?v=abc123"},
        )

    async def fake_synthesize(text, language_code=None, speaker=None):
        return b"fake-opus-bytes"

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(chat_core, "process_message", fake_process_message)
    monkeypatch.setattr(sarvam_client, "synthesize_speech", fake_synthesize)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={token}") as ws:
            ws.send_bytes(b"some-audio")
            ws.receive_json()  # transcript

            video_frame = ws.receive_json()
            assert video_frame == {
                "type": "video", "title": "Photosynthesis Explained", "url": "https://youtube.com/watch?v=abc123",
            }

            reply_frame = ws.receive_json()
            assert reply_frame["type"] == "reply_text"
            assert "youtube.com" not in reply_frame["text"]  # never leaked into the spoken/text reply
    finally:
        app.dependency_overrides.clear()


def test_failed_sketch_generation_does_not_send_diagram_or_fail_the_turn(db_session, monkeypatch):
    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    token = _ticket(student)

    async def fake_transcribe(audio_bytes, filename="voice_note.ogg"):
        return "draw a plant cell"

    async def fake_process_message(db, student, message_text):
        return ChatTurnResult(reply_text="Here's the plant cell.", image_prompt="a plant cell")

    async def fake_generate_sketch_scene_fails(prompt):
        return None, None, None

    async def fake_synthesize(text, language_code=None, speaker=None):
        return b"fake-opus-bytes"

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(chat_core, "process_message", fake_process_message)
    monkeypatch.setattr(sketch_client, "generate_sketch_scene", fake_generate_sketch_scene_fails)
    monkeypatch.setattr(sarvam_client, "synthesize_speech", fake_synthesize)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={token}") as ws:
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
    token = _ticket(student)

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
        with client.websocket_connect(f"/ws/voice-call?ticket={token}") as ws:
            ws.send_bytes(b"some-audio")
            ws.receive_json()  # transcript
            ws.receive_json()  # reply_text
            frame = ws.receive_json()
            assert frame == {"type": "tts_failed"}
    finally:
        app.dependency_overrides.clear()


def test_mindmap_topic_reply_sends_diagram_frame_before_reply_text(db_session, monkeypatch):
    """Mirror of the image_prompt test above: a mindmap_topic goes through
    app.services.mindmap (one Claude call, billed as mindmap_generate) and
    is sent as the same "diagram" frame, before reply_text."""
    from app.services import mindmap

    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    token = _ticket(student)

    scene = [
        {"type": "ellipse", "x": 200, "y": 150, "rx": 60, "ry": 26, "color": "#23252b", "width": 2.5, "fill": "#f3efe6"},
        {"type": "branch", "points": [[262, 150], [293, 160], [324, 150]], "color": "#d3543a", "width": 4,
         "label": "Inputs", "level": 1, "label_at": "mid"},
    ]

    async def fake_transcribe(audio_bytes, filename="voice_note.ogg"):
        return "mind map of photosynthesis"

    async def fake_process_message(db, student, message_text):
        return ChatTurnResult(reply_text="Here's your mind map.", mindmap_topic="photosynthesis")

    async def fake_generate_mindmap_scene(topic):
        assert topic == "photosynthesis"
        return scene, LLMResult(text="{}", model="claude-sonnet-4-6", input_tokens=5, output_tokens=5)

    async def fail_if_called(prompt):
        raise AssertionError("sketch_client must not be used for a mind map turn")

    async def fake_synthesize(text, language_code=None, speaker=None):
        return b"fake-opus-bytes"

    billed = []
    real_record = cost_tracker.record_claude_usage

    def spy_record(db, model, input_tokens, output_tokens, student_id, **kwargs):
        billed.append(kwargs.get("feature"))
        return real_record(db, model, input_tokens, output_tokens, student_id, **kwargs)

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(chat_core, "process_message", fake_process_message)
    monkeypatch.setattr(mindmap, "generate_mindmap_scene", fake_generate_mindmap_scene)
    monkeypatch.setattr(sketch_client, "generate_sketch_scene", fail_if_called)
    monkeypatch.setattr(sarvam_client, "synthesize_speech", fake_synthesize)
    monkeypatch.setattr(cost_tracker, "record_claude_usage", spy_record)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={token}") as ws:
            ws.send_bytes(b"some-audio")
            ws.receive_json()  # transcript

            diagram_frame = ws.receive_json()
            assert diagram_frame == {"type": "diagram", "scene": scene}

            reply_frame = ws.receive_json()
            assert reply_frame["type"] == "reply_text"
    finally:
        app.dependency_overrides.clear()
    assert billed == ["mindmap_generate"]


def test_failed_mindmap_generation_does_not_send_diagram_or_fail_the_turn(db_session, monkeypatch):
    from app.services import mindmap

    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    token = _ticket(student)

    async def fake_transcribe(audio_bytes, filename="voice_note.ogg"):
        return "mind map of gravity"

    async def fake_process_message(db, student, message_text):
        return ChatTurnResult(reply_text="Here's gravity.", mindmap_topic="gravity")

    async def fake_generate_mindmap_scene_fails(topic):
        return None, None

    async def fake_synthesize(text, language_code=None, speaker=None):
        return b"fake-opus-bytes"

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(chat_core, "process_message", fake_process_message)
    monkeypatch.setattr(mindmap, "generate_mindmap_scene", fake_generate_mindmap_scene_fails)
    monkeypatch.setattr(sarvam_client, "synthesize_speech", fake_synthesize)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={token}") as ws:
            ws.send_bytes(b"some-audio")
            ws.receive_json()  # transcript
            reply_frame = ws.receive_json()
            assert reply_frame["type"] == "reply_text"
            assert ws.receive_bytes() == b"fake-opus-bytes"
    finally:
        app.dependency_overrides.clear()


# --- Ticket auth (the ?token= JWT-in-URL is gone — audit Sept 2026, H1) ---


def test_long_lived_student_token_is_no_longer_accepted(db_session):
    """The 7-day student JWT must never work on this URL again, under either query-param name."""
    student = _make_student(db_session, voice_enabled=True)
    token = create_student_access_token(student.id)
    client = _client(db_session)
    try:
        for url in (f"/ws/voice-call?token={token}", f"/ws/voice-call?ticket={token}"):
            with client.websocket_connect(url) as ws:
                frame = ws.receive_json()
                assert frame["type"] == "error"
                assert "session" in frame["message"].lower()
    finally:
        app.dependency_overrides.clear()


def test_ticket_endpoint_mints_a_short_lived_single_use_ticket(db_session, monkeypatch):
    student = _make_student(db_session, voice_enabled=True)
    token = create_student_access_token(student.id)
    client = _client(db_session)
    try:
        unauthenticated = client.post("/student-app/voice-call/ticket")
        assert unauthenticated.status_code in (401, 403)

        response = client.post("/student-app/voice-call/ticket", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        body = response.json()
        payload = jwt.decode(body["ticket"], options={"verify_signature": False})
        assert payload["type"] == "voice_ticket"
        assert payload["sub"] == str(student.id)
        assert payload["jti"]
        assert payload["exp"] - payload["iat"] == body["expires_in"] == 60

        # ...and it actually opens a call.
        cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
        _mock_happy_turn(monkeypatch)
        with client.websocket_connect(f"/ws/voice-call?ticket={body['ticket']}") as ws:
            ws.send_bytes(b"some-audio")
            assert ws.receive_json()["type"] == "transcript"
    finally:
        app.dependency_overrides.clear()


def test_expired_ticket_is_rejected(db_session):
    student = _make_student(db_session, voice_enabled=True)
    expired = create_voice_call_ticket(student, ttl_seconds=-5)
    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={expired}") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error"
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    finally:
        app.dependency_overrides.clear()


def test_ticket_cannot_be_reused(db_session, monkeypatch):
    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    _mock_happy_turn(monkeypatch)
    ticket = _ticket(student)
    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={ticket}") as ws:
            ws.send_bytes(b"some-audio")
            assert ws.receive_json()["type"] == "transcript"  # first use: accepted
        with client.websocket_connect(f"/ws/voice-call?ticket={ticket}") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert "session" in frame["message"].lower()
    finally:
        app.dependency_overrides.clear()


def test_ticket_minted_before_logout_everywhere_is_rejected(db_session):
    """token_version travels on the ticket exactly as on a normal token."""
    student = _make_student(db_session, voice_enabled=True)
    ticket = _ticket(student)
    student.token_version = (student.token_version or 0) + 1
    db_session.commit()
    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={ticket}") as ws:
            assert ws.receive_json()["type"] == "error"
    finally:
        app.dependency_overrides.clear()


# --- Per-turn gates + lock + rollback (audit Sept 2026, P1) ---


def _mock_happy_turn(monkeypatch, reply="Hi there!"):
    async def fake_transcribe(audio_bytes, filename="voice_note.ogg"):
        return "hello"

    async def fake_process_message(db, student, message_text):
        return ChatTurnResult(reply_text=reply)

    async def fake_synthesize(text, language_code=None, speaker=None):
        return b"fake-opus-bytes"

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(chat_core, "process_message", fake_process_message)
    monkeypatch.setattr(sarvam_client, "synthesize_speech", fake_synthesize)


def test_churned_school_gets_the_same_notice_as_whatsapp_and_the_call_is_closed(db_session, monkeypatch):
    student = _make_student(db_session, voice_enabled=True, sales_status="churned")
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    _mock_happy_turn(monkeypatch)

    async def fail_if_transcribed(audio_bytes, filename="voice_note.ogg"):
        raise AssertionError("no STT spend for a churned school's student")

    monkeypatch.setattr(sarvam_client, "transcribe_audio", fail_if_transcribed)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={_ticket(student)}") as ws:
            ws.send_bytes(b"some-audio")
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert "on hold" in frame["message"]
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1008
    finally:
        app.dependency_overrides.clear()


def test_expired_pilot_closes_the_call(db_session, monkeypatch):
    from app.services import school_billing

    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    _mock_happy_turn(monkeypatch)
    monkeypatch.setattr(school_billing, "is_centre_pilot_expired", lambda db, centre_id: True)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={_ticket(student)}") as ws:
            ws.send_bytes(b"some-audio")
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert "pilot has ended" in frame["message"]
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    finally:
        app.dependency_overrides.clear()


def test_platform_spend_cap_closes_the_call(db_session, monkeypatch):
    from app.routers import voice_call

    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    _mock_happy_turn(monkeypatch)

    async def cap_hit(db):
        return True

    monkeypatch.setattr(voice_call, "platform_spend_cap_exceeded", cap_hit)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={_ticket(student)}") as ws:
            ws.send_bytes(b"some-audio")
            frame = ws.receive_json()
            assert frame == {"type": "error", "message": "We're busy right now, please try again in a bit."}
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    finally:
        app.dependency_overrides.clear()


def test_rate_limited_turn_is_skipped_but_the_call_stays_open(db_session, monkeypatch):
    from app.routers import voice_call

    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    _mock_happy_turn(monkeypatch)
    limited = [True, False]

    async def fake_is_rate_limited(phone):
        return limited.pop(0)

    monkeypatch.setattr(voice_call, "is_rate_limited", fake_is_rate_limited)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={_ticket(student)}") as ws:
            ws.send_bytes(b"some-audio")
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert "a bit fast" in frame["message"]
            # Still open: the next turn goes through normally.
            ws.send_bytes(b"some-audio")
            assert ws.receive_json()["type"] == "transcript"
    finally:
        app.dependency_overrides.clear()


def test_each_turn_runs_under_the_per_student_lock(db_session, monkeypatch):
    from app.routers import voice_call

    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    _mock_happy_turn(monkeypatch)
    locked_phones = []

    @__import__("contextlib").asynccontextmanager
    async def fake_lock(phone):
        locked_phones.append(phone)
        yield

    monkeypatch.setattr(voice_call, "student_turn_lock", fake_lock)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={_ticket(student)}") as ws:
            ws.send_bytes(b"some-audio")
            ws.receive_json()  # transcript
            ws.receive_json()  # reply_text
            ws.receive_bytes()
    finally:
        app.dependency_overrides.clear()
    assert locked_phones == [student.phone]


def test_weekly_voice_cap_degrades_the_reply_to_text_only(db_session, monkeypatch):
    """Past FEATURE_LIMITS["voice"], no TTS is synthesized — the client gets tts_failed, exactly as for a failed TTS."""
    from app.business_rules import FEATURE_LIMITS

    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    for _ in range(FEATURE_LIMITS["voice"]["max"]):
        cost_tracker.record_char_usage(db_session, "sarvam_tts", 100, student.id)
    _mock_happy_turn(monkeypatch)

    async def fail_if_synthesized(text, language_code=None, speaker=None):
        raise AssertionError("TTS must not run past the weekly voice cap")

    monkeypatch.setattr(sarvam_client, "synthesize_speech", fail_if_synthesized)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={_ticket(student)}") as ws:
            ws.send_bytes(b"some-audio")
            assert ws.receive_json()["type"] == "transcript"
            assert ws.receive_json()["type"] == "reply_text"
            assert ws.receive_json() == {"type": "tts_failed"}
    finally:
        app.dependency_overrides.clear()


def test_a_turn_that_fails_mid_flush_does_not_poison_the_next_turn(db_session, monkeypatch):
    """
    Regression: without db.rollback() in the turn's error handler, a
    failed flush left the session in "must roll back" state and every
    later turn on the same call raised PendingRollbackError.
    """
    from app.models.core import ChatHistory

    student = _make_student(db_session, voice_enabled=True)
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")
    _mock_happy_turn(monkeypatch)
    calls = []

    async def process_message_fails_in_flush_then_works(db, student_, message_text):
        calls.append(1)
        if len(calls) == 1:
            # Violates ChatHistory's role CHECK constraint -> IntegrityError on flush.
            db.add(ChatHistory(student_id=student_.id, role="bogus", message="x", agent="tutor"))
            db.flush()
        return ChatTurnResult(reply_text="Second time lucky.")

    monkeypatch.setattr(chat_core, "process_message", process_message_fails_in_flush_then_works)

    client = _client(db_session)
    try:
        with client.websocket_connect(f"/ws/voice-call?ticket={_ticket(student)}") as ws:
            ws.send_bytes(b"some-audio")
            assert ws.receive_json()["type"] == "transcript"
            failed = ws.receive_json()
            assert failed["type"] == "error"
            assert "went wrong" in failed["message"]

            ws.send_bytes(b"some-audio")
            assert ws.receive_json()["type"] == "transcript"
            reply = ws.receive_json()
            assert reply == {"type": "reply_text", "text": "Second time lucky."}
            assert ws.receive_bytes() == b"fake-opus-bytes"
    finally:
        app.dependency_overrides.clear()
    assert len(calls) == 2
