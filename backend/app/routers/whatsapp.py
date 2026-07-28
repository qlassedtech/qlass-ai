import asyncio
from collections import defaultdict

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
from app.services.ocr_client import extract_text_from_image
from app.services.image_client import generate_image
from app.services.document_client import extract_text_from_document
from app.agents.tutor_agent import TutorAgent

router = APIRouter()
tutor_agent = TutorAgent()

HISTORY_TURNS = 12  # ~6 back-and-forth exchanges of prior context
WEAK_TOPICS_LIMIT = 5
OFF_LEVEL_SUGGEST_THRESHOLD = 3  # consecutive off-level questions before suggesting a class update

_AFFIRMATIVE_WORDS = {"yes", "yeah", "yep", "sure", "ok", "okay", "haan", "y", "please", "yup"}

# Per-student locks so messages from the same student are handled one at a
# time, in order. Without this, a student sending two messages a few seconds
# apart (common — they re-ask or add a follow-up before the first reply has
# come back) spawns two concurrent background tasks that each read the chat
# history before the other has committed its reply, so both answer against
# the same stale context — producing duplicate/overlapping/out-of-order
# replies instead of the second one building on the first.
_student_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _looks_affirmative(text: str) -> bool:
    words = set(text.strip().lower().split())
    return bool(words & _AFFIRMATIVE_WORDS)


def _get_or_create_student(db: Session, from_phone: str) -> Student:
    student = db.query(Student).filter(Student.phone == from_phone).first()
    if student is None:
        student = Student(name="New Student", phone=from_phone)
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
            import traceback
            traceback.print_exc()
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

    parsed = parse_incoming_message(payload)
    if parsed:
        from_phone, message_text = parsed
        student = _get_or_create_student(db, from_phone)
    else:
        audio_parsed = parse_incoming_audio(payload)
        image_parsed = parse_incoming_image(payload) if not audio_parsed else None
        document_parsed = parse_incoming_document(payload) if not (audio_parsed or image_parsed) else None

        if audio_parsed:
            from_phone, media_url = audio_parsed
            student = _get_or_create_student(db, from_phone)
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
        elif image_parsed:
            from_phone, media_url = image_parsed
            student = _get_or_create_student(db, from_phone)
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
        elif document_parsed:
            from_phone, media_url = document_parsed
            student = _get_or_create_student(db, from_phone)
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
        # Claude decides the reply language itself (sees the actual message
        # and full context, unlike a stateless per-message detection call) —
        # see the "lang" field in the tracking tag. It also decides whether a
        # diagram is warranted (only if image generation is enabled for this
        # student), returning an image_prompt when so, and whether a text
        # message should selectively get a voice reply (only if explicitly
        # asked for — most text stays text).
        result = await tutor_agent.respond(
            student.as_profile_dict(),
            message_text,
            history,
            weak_topics,
            image_generation_enabled=student.has_feature("image_generation"),
            voice_enabled=student.has_feature("voice"),
        )
        reply_text = result["reply"]
        detected_lang = result["lang"]
        image_prompt = result["image_prompt"]
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
        translated = await translate_with_claude(reply_text, detected_lang)
        if not translated:
            translated = await translate_text(reply_text, "en-IN", detected_lang)
        if translated:
            outgoing_text = translated

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
        audio_reply = await synthesize_speech(outgoing_text, detected_lang)
        if audio_reply:
            send_result = await send_whatsapp_audio(from_phone, audio_reply)
            if not send_result.get("sent"):
                print(f"[whatsapp] send_whatsapp_audio failed for {from_phone}: {send_result}")
            else:
                # Also send the text transcript, in the same language as the
                # voice note, so the student has it in writing too.
                await send_whatsapp_message(from_phone, outgoing_text)
    if image_prompt:
        image_bytes = await generate_image(image_prompt)
        if image_bytes:
            image_result = await send_whatsapp_image(from_phone, image_bytes, caption=outgoing_text)
            if not image_result.get("sent"):
                print(f"[whatsapp] send_whatsapp_image failed for {from_phone}: {image_result}")
            elif not send_result:
                # Voice reply (if any) already covered the spoken explanation;
                # the image send itself counts as having delivered something,
                # so only treat this as the "send_result" when there wasn't
                # already a successful voice send above.
                send_result = image_result
    if not send_result or not send_result.get("sent"):
        fallback_result = await send_whatsapp_message(from_phone, outgoing_text)
        if not fallback_result.get("sent"):
            print(f"[whatsapp] send_whatsapp_message fallback also failed for {from_phone}: {fallback_result}")
