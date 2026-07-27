from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core import Student, ChatHistory
from app.services.whatsapp_client import (
    parse_incoming_message,
    parse_incoming_audio,
    download_media,
    send_whatsapp_message,
    send_whatsapp_audio,
    verify_webhook_auth,
)
from app.services.profile_builder import next_missing_field, should_ask_this_turn, clean_answer, looks_like_answer
from app.services.sarvam_client import transcribe_audio, synthesize_speech, detect_language, translate_text
from app.agents.tutor_agent import TutorAgent

router = APIRouter()
tutor_agent = TutorAgent()

HISTORY_TURNS = 12  # ~6 back-and-forth exchanges of prior context


@router.post("/webhook")
async def receive_message(request: Request, db: Session = Depends(get_db)):
    """
    Full working flow:
    Wati -> parse (text or voice note) -> find/create Student -> TutorAgent
    (calls LLM) -> save chat_history -> send reply back via Wati's API,
    as text or a synthesized voice note matching the input modality.

    Unlike Meta's Cloud API, Wati has no GET verification handshake — you just
    point Wati's dashboard webhook setting at this URL.
    """
    if not verify_webhook_auth(request.headers.get("authorization")):
        raise HTTPException(status_code=403, detail="Invalid webhook auth")

    payload = await request.json()
    is_voice_input = False

    parsed = parse_incoming_message(payload)
    if parsed:
        from_phone, message_text = parsed
    else:
        audio_parsed = parse_incoming_audio(payload)
        if not audio_parsed:
            return {"received": True, "handled": False}

        from_phone, message_id = audio_parsed
        audio_bytes = await download_media(message_id)
        if audio_bytes is None:
            return {"received": True, "handled": False, "reason": "could not download voice note"}

        message_text = await transcribe_audio(audio_bytes)
        if not message_text:
            return {"received": True, "handled": False, "reason": "speech-to-text not configured or failed"}
        is_voice_input = True

    if not message_text:
        return {"received": True, "handled": False, "reason": "no text body"}

    # Find or create the student profile (Phase 3/4)
    student = db.query(Student).filter(Student.phone == from_phone).first()
    if student is None:
        student = Student(name="New Student", phone=from_phone)
        db.add(student)
        db.commit()
        db.refresh(student)

    # Detect the language the student is actually writing/speaking in (per
    # message, since a student may switch languages), so the final reply can
    # be translated back into it. Claude always reasons/answers in English;
    # Sarvam handles the Indian-language expression.
    #
    # Only trust the guess when it's backed by an actual native script
    # (Devanagari, Gurmukhi, Tamil, ...) OR is confidently English — detecting
    # *which Indian language* Romanized/Latin-script text is (e.g. "kyunki
    # jagah") is inherently ambiguous (Hindi/Punjabi/etc. romanize similarly)
    # and caused a real misfire (Hindi conversation flipped to Punjabi
    # mid-thread on a Romanized reply). English itself is reliably
    # distinguishable even in Latin script, so it's still trusted.
    # For ambiguous Romanized-non-English input, keep whatever language this
    # student has already established rather than guessing.
    detection = await detect_language(message_text)
    if detection:
        lang, script = detection
        is_ambiguous_romanized = (script or "").lower() == "latn" and not lang.startswith("en")
        if not is_ambiguous_romanized:
            student.preferred_language = lang
            db.commit()
            detected_lang = lang
        else:
            detected_lang = student.preferred_language if student.preferred_language != "en" else None
    else:
        detected_lang = student.preferred_language if student.preferred_language != "en" else None

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

    if student.pending_profile_field and looks_like_answer(student.pending_profile_field, message_text):
        # This message is the answer to a profile question we asked last turn,
        # not a new tutoring question.
        setattr(student, student.pending_profile_field, clean_answer(student.pending_profile_field, message_text))
        student.pending_profile_field = None
        db.commit()
        reply_text = "Got it, thanks! 👍 What else can I help you with?"
    else:
        # Either there was no pending question, or the student ignored it and
        # asked something else — drop the pending question either way so we
        # don't keep misreading their answers, then answer normally.
        if student.pending_profile_field:
            student.pending_profile_field = None
            db.commit()

        # Route to the Tutor Agent -> real LLM call, with conversation history
        reply_text = await tutor_agent.respond(student.as_profile_dict(), message_text, history)

        user_message_count = (
            db.query(ChatHistory)
            .filter(ChatHistory.student_id == student.id, ChatHistory.role == "user")
            .count()
        )
        if should_ask_this_turn(user_message_count):
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
    # Falls back to the English reply if not configured, not needed
    # (student wrote in English), or the translation call fails.
    outgoing_text = reply_text
    if detected_lang and not detected_lang.startswith("en"):
        translated = await translate_text(reply_text, "en-IN", detected_lang)
        if translated:
            outgoing_text = translated

    # Send back over Wati — as a voice note if the student spoke, else text
    send_result = None
    if is_voice_input:
        audio_reply = await synthesize_speech(outgoing_text, detected_lang)
        if audio_reply:
            send_result = await send_whatsapp_audio(from_phone, audio_reply)
    if send_result is None:
        send_result = await send_whatsapp_message(from_phone, outgoing_text)

    return {
        "received": True,
        "handled": True,
        "reply": reply_text,
        "outgoing_text": outgoing_text,
        "detected_language": detected_lang,
        "whatsapp_send": send_result,
    }
