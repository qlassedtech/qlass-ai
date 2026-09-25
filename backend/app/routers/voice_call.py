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
  - {"type": "diagram", "scene": [...]}          — sent only if the
    tutor's reply carried an image_prompt AND sketch generation succeeded.
    Sent BEFORE reply_text so the client can start the sketch animation
    while the reply audio is still being synthesized. `scene` is a JSON
    array of drawing primitives on a fixed 400x300 coordinate space (see
    app.services.sketch_client's module docstring for the exact element
    schema) for the client to render/animate with rough.js — order in the
    array is draw order. Never sent if there was no image_prompt this
    turn, or if sketch generation failed; the client should simply not
    expect a diagram in that case, exactly like a missing tts_failed frame
    doesn't imply anything went wrong. Producing this scene is now a
    two-Claude-call pipeline under the hood (generation, then a
    self-critique pass for content/domain correctness — see
    sketch_client.generate_sketch_scene's docstring) plus a deterministic
    text-collision repair pass; both calls are billed together as a
    single combined LLMResult at this router's own call site below, so
    that billing code didn't need to change shape even though the actual
    Claude spend per diagram roughly doubled.
    The same frame also carries a MIND MAP when the tutor's reply carried
    a mindmap_topic instead of an image_prompt (never both): the scene
    then comes from app.services.mindmap (one Claude call for the
    content, deterministic code for the geometry) and, on top of the
    plain sketch primitives, uses these element types/fields the client
    must render — see mindmap.py's module docstring for the exact rules:
      {"type": "branch", "points": [[sx,sy],[cx,cy],[ex,ey]], "color",
       "width", "label", "level": 1|2, "label_at": "mid"|"end"}
          a quadratic Bezier (start, control, end) stroked in `color` at
          `width`; level-1 labels are drawn centred at the curve's t=0.5
          point, baseline 8px above, bold 13px in the branch colour;
          level-2 labels centred at the END point, baseline 6px above,
          normal 11px #333333.
      "ellipse" may carry "color"/"width"/"fill"; "text" may carry
      "size"/"weight"/"align" ("center" = centred on x,y)/"color".
  - {"type": "video", "title": "...", "url": "..."}  — sent only if the
    tutor's reply carried a video suggestion (same mechanism as WhatsApp's
    own "📺 <title>\n<url>" follow-up message — see routers.whatsapp).
    Sent alongside reply_text (order between the two doesn't matter, unlike
    diagram/reply_text above) — the client shows it as a clickable link in
    the transcript log, since embedding/autoplaying a YouTube video inside
    this page is out of scope. Never read aloud (kept out of reply_text
    for exactly the same reason app.services.chat_core keeps it out of
    reply_text for every other channel: a spoken-aloud raw URL is useless).
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
header, so the credential has to travel in the URL — and URLs are logged
(nginx access log, browser history). It is therefore NOT the 7-day student
JWT (which is what used to go here — audit, Sept 2026: H1) but a
short-lived, single-use ticket: the client first calls
POST /student-app/voice-call/ticket with its normal bearer auth, then
connects to `wss://.../ws/voice-call?ticket=...` within 60 seconds. The
ticket is validated (signature, type, expiry, single-use jti) and the
student loaded with the same lookup/token_version check every REST student
endpoint applies — see app.services.student_auth.consume_voice_call_ticket.
`?token=` is no longer accepted. Anything wrong with the ticket, or the
account lacking the "voice" feature flag, closes the socket with code 1008
right after connecting.

Per-turn gates: every turn takes the same per-student lock WhatsApp and
the web app take (so a voice turn and a WhatsApp message from the same
student can't overdraft one wallet in parallel — see
app.services.rate_limit.student_turn_lock) and applies the same gates
those channels apply before spending anything: churned school, expired
pilot, the platform-wide daily spend cap, the per-student message rate
limit, and the wallet balance. The weekly voice-reply soft cap
(app.business_rules.FEATURE_LIMITS["voice"]) applies to the reply audio
exactly as it does to a WhatsApp voice reply: past the cap the turn
degrades to text-only (a tts_failed frame) instead of synthesizing.
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import audio_qa, cost_tracker, chat_core, mindmap, sarvam_client, school_billing, sketch_client
from app.services.rate_limit import is_rate_limited, platform_spend_cap_exceeded, student_turn_lock
from app.services.student_auth import consume_voice_call_ticket

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
# Same wording the WhatsApp and web/app channels use for these gates (see
# app.routers.whatsapp / app.routers.student_app._reply_to_locked).
_CHURNED_MESSAGE = (
    "Your school's Skoolgpt account is currently on hold — ask your school to contact Skoolgpt, "
    "or top up your own AI credits directly to keep chatting with me!"
)
_PILOT_EXPIRED_MESSAGE = (
    "Your school's Skoolgpt pilot has ended. Ask your school to continue the programme, "
    "or top up your own AI credits to keep learning!"
)
_PLATFORM_BUSY_MESSAGE = "We're busy right now, please try again in a bit."
_RATE_LIMITED_MESSAGE = "You're sending messages a bit fast — please wait a moment before sending more."


async def _terminal_gate_message(db: Session, student) -> str | None:
    """
    The "this call is over" gates every spending channel applies, in the
    same order WhatsApp applies them: churned school, expired pilot,
    platform daily spend cap, wallet balance. Returns the message to send
    before closing with 1008, or None when the turn may proceed.
    """
    if school_billing.is_centre_churned(db, student.centre_id) and not cost_tracker.has_independent_payment(db, student.id):
        return _CHURNED_MESSAGE
    if school_billing.is_centre_pilot_expired(db, student.centre_id) and not cost_tracker.has_independent_payment(db, student.id):
        return _PILOT_EXPIRED_MESSAGE
    if await platform_spend_cap_exceeded(db):
        return _PLATFORM_BUSY_MESSAGE
    if not cost_tracker.has_credits(db, student.id):
        return _OUT_OF_CREDITS_MESSAGE
    return None


@router.websocket("/ws/voice-call")
async def voice_call_ws(websocket: WebSocket, db: Session = Depends(get_db)):
    await websocket.accept()

    ticket = websocket.query_params.get("ticket")
    student = await consume_voice_call_ticket(ticket, db) if ticket else None
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

            # Per-student rate limit, same as WhatsApp — a turn over the
            # limit is simply not processed (no STT spend); the connection
            # stays open, it's not a failed turn.
            if await is_rate_limited(student.phone):
                await websocket.send_json({"type": "error", "message": _RATE_LIMITED_MESSAGE})
                continue

            try:
                # Same per-student lock as WhatsApp/web — the credit check
                # through the deductions inside _handle_turn is one
                # critical section, so a concurrent WhatsApp message from
                # this student can't pass has_credits() in parallel.
                async with student_turn_lock(student.phone):
                    gate_message = await _terminal_gate_message(db, student)
                    if gate_message is not None:
                        await websocket.send_json({"type": "error", "message": gate_message})
                        await websocket.close(code=_POLICY_VIOLATION)
                        return
                    turn_ok = await _handle_turn(websocket, db, student, audio_bytes)
            except WebSocketDisconnect:
                raise
            except Exception:
                # A single turn's own bug/transient failure (e.g. Sarvam
                # blipping, an unexpected exception in process_message)
                # must not take down the whole call — only the STT/TTS
                # helpers' own None-return failure paths are the "normal"
                # failure case; this except is the backstop for anything
                # unexpected so it degrades to a retryable error frame too.
                #
                # rollback() is essential: a turn that died mid-flush
                # leaves the session in a "must roll back" state, and
                # without this every later turn on this same call failed
                # too (PendingRollbackError) — one bad turn poisoned the
                # rest of the call (audit, Sept 2026).
                # rollback() FIRST — even reading student.id below can
                # trigger an attribute refresh on a session that is
                # mid-failed-flush, which itself raises.
                db.rollback()
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
    # Decoding runs in a worker thread — librosa is CPU-bound and would
    # otherwise stall every other call/request on this event loop.
    duration_seconds = await asyncio.to_thread(audio_qa.get_duration_seconds, audio_bytes)
    cost_tracker.record_minute_usage(db, "sarvam_stt", duration_seconds / 60, student.id)

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

    # Sent as its own "video" frame (see module docstring) — the client
    # shows it as a clickable link, same spirit as WhatsApp's own separate
    # "📺 <title>" follow-up message (routers.whatsapp), just never spoken
    # aloud by TTS (kept out of reply_text, same reason chat_core keeps it
    # out of reply_text for every channel).
    if result.video:
        await websocket.send_json({"type": "video", "title": result.video["title"], "url": result.video["url"]})

    if result.image_prompt:
        # A failed/unusable sketch degrades exactly like a failed TTS call
        # does: nothing diagram-related is sent, and the turn continues
        # normally with reply_text/audio — this must never fail the turn.
        #
        # generate_sketch_scene makes two Claude calls per diagram
        # (generation + a self-critique/domain-correctness pass — see its
        # docstring) and now returns each pass's usage separately so they
        # can be billed — and tagged with their own feature label — one at
        # a time, instead of billing one call with their costs summed
        # together (which would make it impossible to see what the
        # critique pass alone costs — see
        # app.services.analytics.get_ai_cost_breakdown).
        scene, generation_result, critique_result = await sketch_client.generate_sketch_scene(result.image_prompt)
        if scene:
            cost_tracker.record_claude_usage(
                db, generation_result.model, generation_result.input_tokens, generation_result.output_tokens,
                student.id, cache_write_tokens=generation_result.cache_write_tokens,
                cache_read_tokens=generation_result.cache_read_tokens, feature="diagram_generate",
            )
            if critique_result is not None:
                cost_tracker.record_claude_usage(
                    db, critique_result.model, critique_result.input_tokens, critique_result.output_tokens,
                    student.id, cache_write_tokens=critique_result.cache_write_tokens,
                    cache_read_tokens=critique_result.cache_read_tokens, feature="diagram_critique",
                )
            await websocket.send_json({"type": "diagram", "scene": scene})
        else:
            logger.info(
                "voice_call: sketch generation failed/unusable for student_id=%s, image_prompt=%r",
                student.id, result.image_prompt,
            )

    if result.mindmap_topic:
        # Same "diagram" frame as above, but the scene comes from
        # app.services.mindmap (one Claude call for the content, code for
        # the geometry) and uses the coloured "branch"/styled elements the
        # client renderer understands — see the module docstring. Failure
        # degrades exactly like a failed sketch: no frame, turn continues.
        scene, generation_result = await mindmap.generate_mindmap_scene(result.mindmap_topic)
        if scene:
            if generation_result is not None:
                cost_tracker.record_claude_usage(
                    db, generation_result.model, generation_result.input_tokens, generation_result.output_tokens,
                    student.id, cache_write_tokens=generation_result.cache_write_tokens,
                    cache_read_tokens=generation_result.cache_read_tokens, feature="mindmap_generate",
                )
            await websocket.send_json({"type": "diagram", "scene": scene})
        else:
            logger.info(
                "voice_call: mind map generation failed/unusable for student_id=%s, topic=%r",
                student.id, result.mindmap_topic,
            )

    await websocket.send_json({"type": "reply_text", "text": result.reply_text})

    # Weekly voice-reply soft cap (FEATURE_LIMITS["voice"]) — the same
    # check chat_core applies before it lets a WhatsApp voice reply
    # through. Past it, the reply is text-only rather than synthesized:
    # reply_text is already sent, so tts_failed is exactly the frame the
    # client expects for "no audio this time, not an error".
    if chat_core.is_voice_reply_over_weekly_cap(db, student):
        logger.info("voice_call: weekly voice cap reached for student_id=%s — text-only reply", student.id)
        await websocket.send_json({"type": "tts_failed"})
        return True

    audio_reply = await sarvam_client.synthesize_speech(result.reply_text, result.detected_lang)
    if not audio_reply:
        await websocket.send_json({"type": "tts_failed"})
        return True  # degraded to text-only, but still a real, usable reply

    cost_tracker.record_char_usage(db, "sarvam_tts", len(result.reply_text), student.id)
    await websocket.send_bytes(audio_reply)
    return True
