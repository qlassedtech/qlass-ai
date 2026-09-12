"""
Real-time(-ish) voice call between a student and the AI tutor — a WebSocket
endpoint backing the /call page on the student web portal (see
frontend/src/pages/Call.tsx).

WhatsApp's Business API has no support for arbitrary two-way real-time
voice calls with a bot, so this feature can't live on WhatsApp like
everything else in this app — it's a page on the student portal instead,
reached via a link a student can be sent on WhatsApp (see
app.services.chat_core's "call my tutor" shortcut).

Deliberately turn-based (push-to-talk), NOT continuous full-duplex
streaming — the target audience (rural Bihar) doesn't reliably have the
sustained low-jitter bandwidth continuous streaming ASR needs, and
turn-based is dramatically simpler to build correctly. One utterance per
binary WebSocket frame; the whole pipeline (STT -> tutor -> TTS) runs once
per frame, then it's the student's turn again.

This file is the first WebSocket code anywhere in this codebase — nothing
else to pattern-match against, so the wire protocol is spelled out here in
full:

Client -> server:
  - One binary frame per utterance: the raw recorded audio for that turn
    (e.g. audio/webm;codecs=opus from the browser's MediaRecorder). No
    text/JSON framing on the way in — the audio blob IS the message.

Server -> client, all text frames carrying a single JSON object except the
one binary audio frame:
  - {"type": "transcript", "text": "..."}       — what Sarvam heard, sent
    as soon as it's back, before the tutor reply is generated, so the UI
    can show it immediately.
  - {"type": "reply_text", "text": "..."}       — the tutor's reply text
    (identical to what WhatsApp/chat would show), sent before the
    corresponding audio so the transcript log updates immediately even if
    TTS is slow or fails.
  - <binary frame>                               — the synthesized reply
    audio (Opus-encoded), sent only if TTS succeeded.
  - {"type": "tts_failed"}                       — sent instead of a binary
    frame when synthesis failed; reply_text was already sent, so the
    client should render a text-only reply, not treat this as a hard error.
  - {"type": "error", "message": "..."}          — this turn could not be
    completed (bad/undecodable audio, Sarvam STT down, out of credits,
    etc.). The connection stays open for another attempt UNLESS this is
    immediately followed by the server closing the socket (out-of-credits,
    or 3 consecutive failed turns) — the client should treat a close code
    of 1008 as "don't bother retrying without fixing something first".

Auth: a browser WebSocket handshake cannot set a custom Authorization
header, so the student JWT travels as a `?token=` query param instead
(`wss://.../ws/voice-call?token=...`) and is validated with the exact same
decode/lookup the REST student endpoints use (see
app.services.student_auth.get_student_by_token) — just read from a query
param instead of a bearer header. Anything wrong with the token, or the
account lacking the "voice" feature flag, closes the socket with code 1008
right after connecting.
"""
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import audio_qa, cost_tracker, chat_core, sarvam_client
from app.services.student_auth import get_student_by_token

logger = logging.getLogger(__name__)

router = APIRouter()

# Auth failure and out-of-credits both close with this — "Policy Violation"
# is the closest standard WebSocket close code to "you can't do that here",
# and lets the client tell "don't retry without fixing something" apart
# from a plain network drop.
_POLICY_VIOLATION = 1008

# One bad turn (a transient Sarvam hiccup, a garbled recording) shouldn't
# kill the call — but repeated failures in a row are a real signal
# something's broken (bad mic, Sarvam outage), not just noise, so the
# connection is closed rather than letting a student sit there tapping the
# talk button forever with nothing usable coming back.
MAX_CONSECUTIVE_FAILURES = 3

_OUT_OF_CREDITS_MESSAGE = (
    "You're out of AI credits for now — top up on your Skoolgpt portal, or send a text/voice note on "
    "WhatsApp instead, to keep learning."
)
_COULD_NOT_HEAR_MESSAGE = "Sorry, I couldn't hear that clearly — please try again."


@router.websocket("/ws/voice-call")
async def voice_call_ws(websocket: WebSocket, db: Session = Depends(get_db)):
    await websocket.accept()

    token = websocket.query_params.get("token")
    student = get_student_by_token(token, db) if token else None
    if student is None:
        await websocket.send_json({"type": "error", "message": "Your session has expired — please log in again."})
        await websocket.close(code=_POLICY_VIOLATION)
        return

    if not student.has_feature("voice"):
        await websocket.send_json(
            {"type": "error", "message": "Voice calling isn't available on your account yet."}
        )
        await websocket.close(code=_POLICY_VIOLATION)
        return

    consecutive_failures = 0
    try:
        while True:
            audio_bytes = await websocket.receive_bytes()

            if not cost_tracker.has_credits(db, student.id):
                await websocket.send_json({"type": "error", "message": _OUT_OF_CREDITS_MESSAGE})
                await websocket.close(code=_POLICY_VIOLATION)
                return

            try:
                turn_ok = await _handle_turn(websocket, db, student, audio_bytes)
            except Exception:
                # A single turn's own bug/transient failure (e.g. Sarvam
                # blipping, an unexpected exception in process_message)
                # must not take down the whole call — only the STT/TTS
                # helpers' own None-return failure paths are the "normal"
                # failure case; this except is the backstop for anything
                # unexpected so it degrades to a retryable error frame too.
                logger.exception("voice_call turn failed for student_id=%s", student.id)
                await websocket.send_json(
                    {"type": "error", "message": "Something went wrong on our end — please try again."}
                )
                turn_ok = False

            if turn_ok:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    await websocket.send_json(
                        {"type": "error", "message": "Having trouble hearing you — please refresh and try again."}
                    )
                    await websocket.close(code=_POLICY_VIOLATION)
                    return
    except WebSocketDisconnect:
        return


async def _handle_turn(websocket: WebSocket, db: Session, student, audio_bytes: bytes) -> bool:
    """
    Runs one full turn (STT -> tutor -> TTS) and sends every frame the
    protocol above promises for it. Returns True if a usable reply
    (transcript + tutor reply, audio or not) was produced, False if the
    turn failed outright (nothing usable came back) — used by the caller
    only to track consecutive failures, not to decide whether to close.
    """
    transcript = await sarvam_client.transcribe_audio(audio_bytes, filename="voice_call.webm")
    if not transcript:
        await websocket.send_json({"type": "error", "message": _COULD_NOT_HEAR_MESSAGE})
        return False

    # Billed the same way a WhatsApp voice note is (see
    # app.routers.whatsapp) — per-minute STT cost from the actual recording
    # duration, applied even though the reply hasn't been generated yet, so
    # a turn that fails downstream (e.g. process_message erroring) still
    # accounts for the real STT spend that already happened.
    cost_tracker.record_minute_usage(db, "sarvam_stt", audio_qa.get_duration_seconds(audio_bytes) / 60, student.id)

    await websocket.send_json({"type": "transcript", "text": transcript})

    # Funnels through the exact same tutoring core WhatsApp text/voice/
    # image/document messages all use — same credits, same chat history,
    # same tutor personality, same TRACK-tag/quiz/menu routing. This call
    # session behaves identically to a WhatsApp conversation from here on.
    result = await chat_core.process_message(db, student, transcript)

    if not result.reply_text:
        # Same "a newer message already superseded this one" case
        # process_message documents for WhatsApp — nothing to say for this
        # turn (rare for a push-to-talk call, since there's no concurrent
        # message from the same student, but handled the same way anyway).
        await websocket.send_json({"type": "error", "message": _COULD_NOT_HEAR_MESSAGE})
        return False

    # image_prompt/video are tutor decisions this MVP call UI doesn't
    # render (see this router's module docstring / the feature's own
    # scoping) — logged, not acted on, so a diagram/video request over a
    # call doesn't just silently disappear without a trace.
    if result.image_prompt or result.video:
        logger.info(
            "voice_call: reply for student_id=%s carried image_prompt/video, not rendered in call UI (out of scope for this MVP)",
            student.id,
        )

    await websocket.send_json({"type": "reply_text", "text": result.reply_text})

    audio_reply = await sarvam_client.synthesize_speech(result.reply_text, result.detected_lang)
    if not audio_reply:
        await websocket.send_json({"type": "tts_failed"})
        return True  # degraded to text-only, but still a real, usable reply

    cost_tracker.record_char_usage(db, "sarvam_tts", len(result.reply_text), student.id)
    await websocket.send_bytes(audio_reply)
    return True
