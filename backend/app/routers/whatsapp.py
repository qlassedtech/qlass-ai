import asyncio
import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.core import Student, ChatHistory, TopicProgress, ProcessedWebhookMessage
from app.services.whatsapp_client import (
    parse_incoming_message,
    parse_incoming_audio,
    parse_incoming_image,
    parse_incoming_document,
    guess_filename_from_media_url,
    download_media,
    send_whatsapp_message,
    send_whatsapp_audio,
    send_whatsapp_image,
    verify_webhook_auth,
)
from app.services.profile_builder import (
    next_missing_field,
    should_ask_this_turn,
    clean_answer,
    looks_like_answer,
    looks_like_confirmation_reply,
)
from app.services.sarvam_client import transcribe_audio, synthesize_speech, translate_text
from app.services.llm_client import translate_with_claude
from app.services.audio_qa import get_duration_seconds, detect_gender_from_pitch
from app.services.ocr_client import extract_text_from_image
from app.services.image_client import generate_image
from app.services.document_client import extract_text_from_document
from app.services.youtube_client import find_best_video
from app.services import cost_tracker
from app.agents.tutor_agent import TutorAgent

logger = logging.getLogger(__name__)

router = APIRouter()
tutor_agent = TutorAgent()

HISTORY_TURNS = 12  # ~6 back-and-forth exchanges of prior context
WEAK_TOPICS_LIMIT = 5
OFF_LEVEL_SUGGEST_THRESHOLD = 3  # consecutive off-level questions before suggesting a class update

_AFFIRMATIVE_WORDS = {"yes", "yeah", "yep", "sure", "ok", "okay", "haan", "y", "please", "yup"}

# Numbers with full feature access during this demo phase (per-account
# feature configuration at real onboarding is still TODO — see
# _get_or_create_student). Everyone else gets a plain-text-only tutor: no
# voice/OCR/image-generation/document costs until they're actually
# provisioned, so a random or leaked number can't run up paid API spend.
FULL_ACCESS_PHONES = {"918789674434", "918460184666", "918252345266"}

# Opposite-gender voice: a female voice for a detected-male student, a male
# voice for a detected-female student. Speaker names are from Sarvam's
# bulbul:v3 roster. Detection is pitch-based (see audio_qa.detect_gender_
# from_pitch) — a coarse, admittedly error-prone heuristic accepted
# deliberately for a first pass rather than not offering it at all.
OPPOSITE_GENDER_SPEAKER = {"male": "priya", "female": "shubh"}

# Per-student locks so messages from the same student are handled one at a
# time, in order. Without this, a student sending two messages a few seconds
# apart (common — they re-ask or add a follow-up before the first reply has
# come back) spawns two concurrent background tasks that each read the chat
# history before the other has committed its reply, so both answer against
# the same stale context — producing duplicate/overlapping/out-of-order
# replies instead of the second one building on the first.
_student_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# Simple sliding-window rate limit per student — protects against a script
# (or a bug on the sender's side) hammering the webhook and burning paid API
# calls faster than any human actually types.
RATE_LIMIT_MAX_MESSAGES = 15
RATE_LIMIT_WINDOW_SECONDS = 60
_message_timestamps: dict[str, deque] = defaultdict(deque)


def _looks_affirmative(text: str) -> bool:
    words = set(text.strip().lower().split())
    return bool(words & _AFFIRMATIVE_WORDS)


def _is_rate_limited(phone: str) -> bool:
    now = time.monotonic()
    window = _message_timestamps[phone]
    while window and now - window[0] > RATE_LIMIT_WINDOW_SECONDS:
        window.popleft()
    window.append(now)
    return len(window) > RATE_LIMIT_MAX_MESSAGES


def _get_or_create_student(db: Session, from_phone: str) -> Student:
    student = db.query(Student).filter(Student.phone == from_phone).first()
    if student is None:
        features = (
            {"voice": True, "ocr": True, "image_generation": True, "documents": True, "youtube_videos": True}
            if from_phone in FULL_ACCESS_PHONES
            else {"voice": False, "ocr": False, "image_generation": False, "documents": False, "youtube_videos": False}
        )
        student = Student(name="New Student", phone=from_phone, features=features)
        db.add(student)
        db.commit()
        db.refresh(student)
    return student


@router.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    """
    Wati -> ack immediately -> process in the background (parse text/voice/
    image/document -> find/create Student -> TutorAgent -> save chat_history
    -> send reply via Wati's API).

    The actual pipeline (STT/LLM/TTS/OCR/image-gen) can take 10+ seconds end
    to end — Wati's webhook caller times out well before that and marks the
    delivery "failed", retrying it even though we'd already generated and
    sent a real reply (confirmed in production: reply appeared in chat
    history ~12s after the message, but Wati still retried and our own
    dedup correctly rejected the retry as already-handled — the *retry* was
    spurious, not the original send). Repeated "failures" like this are also
    what's likely to get a webhook marked "Defective" by Wati and paused
    entirely. Acknowledging fast and doing the real work after the response
    is sent avoids both problems, since sending the reply is already a
    separate outbound call to Wati's API, not dependent on this response.

    Unlike Meta's Cloud API, Wati has no GET verification handshake — you just
    point Wati's dashboard webhook setting at this URL.
    """
    if not verify_webhook_auth(request.headers.get("authorization")):
        raise HTTPException(status_code=403, detail="Invalid webhook auth")

    payload = await request.json()

    # Guard against Wati redelivering/retrying a webhook call for a message
    # we already processed — mark it seen as early as possible, before any
    # real work, so a duplicate delivery is a no-op rather than a second
    # confusing reply to the student. This is a fast, single-insert check,
    # safe to do before acknowledging.
    webhook_message_id = payload.get("whatsappMessageId") or payload.get("id")
    if webhook_message_id:
        db = SessionLocal()
        try:
            db.add(ProcessedWebhookMessage(message_id=webhook_message_id))
            db.commit()
        except IntegrityError:
            db.rollback()
            return {"received": True, "handled": False, "reason": "duplicate webhook delivery, already processed"}
        finally:
            db.close()

    background_tasks.add_task(_process_webhook_payload, payload)
    return {"received": True, "handled": "processing"}


async def _process_webhook_payload(payload: dict) -> None:
    """
    The actual work, run after the webhook response has already been sent.
    Opens its own DB session since the request-scoped one from Depends(get_db)
    would already be closed by the time a background task runs.
    """
    phone = payload.get("waId")
    lock = _student_locks[phone] if phone else asyncio.Lock()
    async with lock:
        db = SessionLocal()
        try:
            await _handle_message(db, payload)
        except Exception:
            # An uncaught exception here previously meant the student got no
            # reply at all and we had no record of why — log the full
            # traceback and let them know something broke instead of leaving
            # them hanging.
            logger.exception("Unhandled error processing webhook payload for %s", phone)
            if phone:
                await send_whatsapp_message(
                    phone, "Sorry, something went wrong on my end — please try sending that again."
                )
        finally:
            db.close()


async def _handle_message(db: Session, payload: dict) -> None:
    # Whether to reply with synthesized speech. Deliberately NOT set just
    # because the student's own input happened to be a voice note — a
    # student who spoke their question may still prefer to read the answer.
    # Only the tutor's explicit "audio=true" decision (student asked for a
    # voice reply) turns this on, further down.
    send_voice_reply = False

    from_phone = payload.get("waId")
    if not from_phone:
        return

    student = _get_or_create_student(db, from_phone)

    if _is_rate_limited(from_phone):
        await send_whatsapp_message(
            from_phone, "You're sending messages a bit fast — please wait a moment before sending more."
        )
        return

    if not cost_tracker.has_credits(db):
        await send_whatsapp_message(
            from_phone, "We're temporarily out of AI credits — please check back a bit later. Sorry for the inconvenience!"
        )
        return

    parsed = parse_incoming_message(payload)
    if parsed:
        _, message_text = parsed
    else:
        audio_parsed = parse_incoming_audio(payload)
        image_parsed = parse_incoming_image(payload) if not audio_parsed else None
        document_parsed = parse_incoming_document(payload) if not (audio_parsed or image_parsed) else None

        if audio_parsed:
            _, media_url = audio_parsed
            if not student.has_feature("voice"):
                await send_whatsapp_message(
                    from_phone, "Voice messages aren't available on your account yet — please type your question instead."
                )
                return

            audio_bytes = await download_media(media_url)
            if audio_bytes is None:
                await send_whatsapp_message(from_phone, "Sorry, I couldn't download that voice note — please try sending it again.")
                return

            message_text = await transcribe_audio(audio_bytes)
            if not message_text:
                await send_whatsapp_message(from_phone, "Sorry, I couldn't understand that voice note — could you try again or type your question?")
                return
            cost_tracker.record_minute_usage(db, "sarvam_stt", get_duration_seconds(audio_bytes) / 60, student.id)

            if student.gender is None:
                # Only estimate once — a cheap local computation (no API
                # cost), used to pick an opposite-gender voice for future
                # replies. Never re-guessed once set.
                detected_gender = detect_gender_from_pitch(audio_bytes)
                if detected_gender:
                    student.gender = detected_gender
                    db.commit()
        elif image_parsed:
            _, media_url = image_parsed
            if not student.has_feature("ocr"):
                await send_whatsapp_message(
                    from_phone, "Photo questions aren't available on your account yet — please type your question instead."
                )
                return

            image_bytes = await download_media(media_url)
            if image_bytes is None:
                await send_whatsapp_message(from_phone, "Sorry, I couldn't download that photo — please try sending it again.")
                return

            message_text = await extract_text_from_image(image_bytes)
            if not message_text:
                await send_whatsapp_message(from_phone, "Sorry, I couldn't read any text in that photo — could you try a clearer picture or type your question?")
                return
            cost_tracker.record_flat_usage(db, "azure_ocr", student.id)
        elif document_parsed:
            _, media_url = document_parsed
            if not student.has_feature("documents"):
                await send_whatsapp_message(
                    from_phone,
                    "PDF/Word file questions aren't available on your account yet — please type your question instead.",
                )
                return

            document_bytes = await download_media(media_url)
            if document_bytes is None:
                await send_whatsapp_message(from_phone, "Sorry, I couldn't download that file — please try sending it again.")
                return

            filename = guess_filename_from_media_url(media_url)
            message_text = extract_text_from_document(document_bytes, filename)
            if not message_text:
                await send_whatsapp_message(from_phone, "Sorry, I couldn't read any text in that file — could you try a different file or type your question?")
                return
        else:
            # Unsupported message type (sticker, location, contact card,
            # button/list reply, etc.) — let the student know rather than
            # going silent.
            await send_whatsapp_message(
                from_phone, "Sorry, I can only handle text, voice notes, photos, and documents right now."
            )
            return

    if not message_text:
        return

    # Pull recent conversation turns *before* saving this message, so the
    # tutor has context but doesn't see this message twice.
    prior_rows = (
        db.query(ChatHistory)
        .filter(ChatHistory.student_id == student.id)
        .order_by(ChatHistory.created_at.desc())
        .limit(HISTORY_TURNS)
        .all()
    )
    history = [{"role": row.role, "content": row.message} for row in reversed(prior_rows)]

    # Save the incoming message
    db.add(ChatHistory(student_id=student.id, role="user", message=message_text, agent="tutor"))
    db.commit()

    image_prompt = None
    video_query = None

    if student.pending_profile_field == "class_confirm" and looks_like_confirmation_reply(message_text):
        # Special case, not a normal profile field — confirming (or declining)
        # the class-update suggestion triggered by a run of off-level questions.
        if _looks_affirmative(message_text):
            student.class_ = student.suggested_class
            reply_text = f"Got it, updated your class to {student.suggested_class}! 👍 What else can I help you with?"
        else:
            reply_text = "No worries, I'll leave it as is! What else can I help you with?"
        student.pending_profile_field = None
        student.suggested_class = None
        db.commit()
        detected_lang = student.preferred_language or "en-IN"
    elif student.pending_profile_field and looks_like_answer(student.pending_profile_field, message_text):
        # This message is the answer to a profile question we asked last turn,
        # not a new tutoring question — no LLM call, so just keep whatever
        # language this student was already using.
        setattr(student, student.pending_profile_field, clean_answer(student.pending_profile_field, message_text))
        student.pending_profile_field = None
        db.commit()
        reply_text = "Got it, thanks! 👍 What else can I help you with?"
        detected_lang = student.preferred_language or "en-IN"
    else:
        # Either there was no pending question, or the student ignored it and
        # asked something else — drop the pending question either way so we
        # don't keep misreading their answers, then answer normally.
        if student.pending_profile_field:
            student.pending_profile_field = None
            student.suggested_class = None
            db.commit()

        # Surface topics this student has previously gotten wrong, so the
        # tutor can naturally revisit them rather than forgetting the moment
        # they scroll out of the ~6-exchange conversation window.
        weak_topic_rows = (
            db.query(TopicProgress.topic)
            .filter(TopicProgress.student_id == student.id, TopicProgress.is_correct.is_(False))
            .order_by(TopicProgress.created_at.desc())
            .limit(WEAK_TOPICS_LIMIT)
            .all()
        )
        weak_topics = list(dict.fromkeys(row[0] for row in weak_topic_rows))  # dedupe, keep order

        # Route to the Tutor Agent -> real LLM call, with conversation history.
        # The reply language is decided by a separate deterministic classify()
        # call inside tutor_agent.respond(), not by the main generation — see
        # the "lang" field it returns. It also decides whether a diagram is
        # warranted (only if image generation is enabled for this student),
        # returning an image_prompt when so, and whether a text message
        # should selectively get a voice reply (only if explicitly asked
        # for — most text stays text).
        result = await tutor_agent.respond(
            student.as_profile_dict(),
            message_text,
            history,
            weak_topics,
            image_generation_enabled=student.has_feature("image_generation"),
            voice_enabled=student.has_feature("voice"),
            video_enabled=student.has_feature("youtube_videos"),
        )
        reply_text = result["reply"]
        detected_lang = result["lang"]
        image_prompt = result["image_prompt"]
        video_query = result["video_query"]
        usage = result["usage"]
        cost_tracker.record_claude_usage(db, usage["main_model"], usage["main_input_tokens"], usage["main_output_tokens"], student.id)
        cost_tracker.record_claude_usage(
            db, usage["classify_model"], usage["classify_input_tokens"], usage["classify_output_tokens"], student.id
        )
        if result["wants_audio_reply"]:
            send_voice_reply = True
        if detected_lang != student.preferred_language:
            student.preferred_language = detected_lang
            db.commit()

        if result["evaluated"]:
            # The check question being evaluated was asked in the *previous*
            # assistant turn, not this one.
            last_assistant_turn = next((h["content"] for h in reversed(history) if h["role"] == "assistant"), None)
            db.add(
                TopicProgress(
                    student_id=student.id,
                    topic=result["topic"] or "unknown",
                    question_text=last_assistant_turn,
                    given_answer=message_text,
                    is_correct=result["correct"],
                )
            )
            db.commit()

        # Track a run of consecutive off-level questions (e.g. registered as
        # class 8 but repeatedly asking Class 12 content) and, after a real
        # pattern rather than a single one-off question, suggest updating
        # their registered class — never on the first occurrence.
        if result["off_level_class"] and result["off_level_class"] != student.class_:
            student.off_level_count = (student.off_level_count or 0) + 1
        else:
            student.off_level_count = 0
        db.commit()

        if (
            result["off_level_class"]
            and student.off_level_count >= OFF_LEVEL_SUGGEST_THRESHOLD
            and not student.pending_profile_field
            and not reply_text.rstrip().endswith("?")
        ):
            level = result["off_level_class"]
            reply_text = (
                f"{reply_text}\n\nBy the way, I've noticed you've been asking Class {level} questions — "
                f"want me to update your registered class to {level}?"
            )
            student.pending_profile_field = "class_confirm"
            student.suggested_class = level
            student.off_level_count = 0
            db.commit()

        user_message_count = (
            db.query(ChatHistory)
            .filter(ChatHistory.student_id == student.id, ChatHistory.role == "user")
            .count()
        )
        # Don't stack a profile question onto a reply that already ends with
        # the tutor's own question (e.g. a check-for-understanding question,
        # or the class-update suggestion above) — a student answering both at
        # once ("Radiant and centripetal") gets entirely swallowed as the
        # profile answer, silently dropping the actual teaching evaluation.
        if should_ask_this_turn(user_message_count) and not reply_text.rstrip().endswith("?"):
            missing = next_missing_field(student)
            if missing:
                field, question = missing
                reply_text = f"{reply_text}\n\n{question}"
                student.pending_profile_field = field
                db.commit()

    # Save the reply — kept in English (what Claude actually said) so future
    # turns give the model a consistent conversation history to reason over.
    db.add(ChatHistory(student_id=student.id, role="assistant", message=reply_text, agent="tutor"))
    db.commit()

    # Translate into the student's language for what actually gets sent.
    # Claude (Haiku) does this first — cheap token cost instead of Sarvam
    # Mayura's per-character billing, which was the single largest cost line
    # item in production. Falls back to Sarvam only if the Claude call fails,
    # and to the plain English reply if not needed (student wrote in
    # English) or both translation paths fail.
    outgoing_text = reply_text
    if detected_lang and not detected_lang.startswith("en"):
        translated_result = await translate_with_claude(reply_text, detected_lang)
        if translated_result:
            outgoing_text = translated_result.text
            cost_tracker.record_claude_usage(
                db, translated_result.model, translated_result.input_tokens, translated_result.output_tokens, student.id
            )
        else:
            translated = await translate_text(reply_text, "en-IN", detected_lang)
            if translated:
                outgoing_text = translated
                cost_tracker.record_char_usage(db, "sarvam_translate", len(reply_text), student.id)

    # Send back over Wati — as a voice note only if the student explicitly
    # asked for an audio reply (never just because their own input was
    # voice), a generated diagram (with the explanation as its caption) if
    # the tutor decided one was warranted, or plain text otherwise. Falls
    # through to text if any richer attempt fails (checking actual success,
    # not just "got a response back" — a failed send still returns a
    # {"sent": False, ...} dict, not None, so checking for None alone would
    # wrongly treat that as handled and never fall back), so the student
    # always gets *something*.
    send_result = None
    if send_voice_reply:
        speaker = OPPOSITE_GENDER_SPEAKER.get(student.gender)
        audio_reply = await synthesize_speech(outgoing_text, detected_lang, speaker=speaker)
        if audio_reply:
            cost_tracker.record_char_usage(db, "sarvam_tts", len(outgoing_text), student.id)
            send_result = await send_whatsapp_audio(from_phone, audio_reply)
            if not send_result.get("sent"):
                logger.error("send_whatsapp_audio failed for %s: %s", from_phone, send_result)
            else:
                # Also send the text transcript, in the same language as the
                # voice note, so the student has it in writing too.
                await send_whatsapp_message(from_phone, outgoing_text)
    if image_prompt:
        image_bytes = await generate_image(image_prompt)
        if image_bytes:
            cost_tracker.record_flat_usage(db, "azure_image", student.id)
            image_result = await send_whatsapp_image(from_phone, image_bytes, caption=outgoing_text)
            if not image_result.get("sent"):
                logger.error("send_whatsapp_image failed for %s: %s", from_phone, image_result)
            elif not send_result:
                # Voice reply (if any) already covered the spoken explanation;
                # the image send itself counts as having delivered something,
                # so only treat this as the "send_result" when there wasn't
                # already a successful voice send above.
                send_result = image_result
    if not send_result or not send_result.get("sent"):
        fallback_result = await send_whatsapp_message(from_phone, outgoing_text)
        if not fallback_result.get("sent"):
            logger.error("send_whatsapp_message fallback also failed for %s: %s", from_phone, fallback_result)

    if video_query:
        video = await find_best_video(video_query)
        if video:
            # Sent as its own follow-up message (Wati has no native rich video
            # embed) rather than folded into the main reply, so the link
            # doesn't get lost inside a long translated paragraph.
            video_result = await send_whatsapp_message(from_phone, f"📺 {video['title']}\n{video['url']}")
            if not video_result.get("sent"):
                logger.error("send video suggestion failed for %s: %s", from_phone, video_result)
