from sqlalchemy.orm import Session

from app.models.core import Student
from app.services.chat_core import process_message


async def process_web_message(db: Session, student: Student, message_text: str) -> str:
    """
    The student web/app/teacher-"My AI Tutor" chat endpoint. Delegates to
    app.services.chat_core, the same routing/tutoring/translation/billing
    logic WhatsApp uses (see app.routers.whatsapp), so a student's
    experience is identical regardless of which channel they use — the
    same chat_history rows, credit ledger, quiz flow, and every intent
    (menu/progress/credit_usage/referral/teacher_help/tutoring) behave the
    same here as on WhatsApp. `message_text` is always plain text by the
    time it gets here — voice notes and photos are transcribed/OCR'd by the
    caller (see app.routers.student_app's send-voice/send-image endpoints)
    before being passed in, exactly like app.routers.whatsapp does.

    Image generation and audio replies are decided by the tutor the same
    way as on WhatsApp (see result.image_prompt/wants_audio_reply on the
    ChatTurnResult), but this endpoint doesn't yet turn those into actual
    media files in the HTTP response — only the text reply is returned.
    """
    result = await process_message(db, student, message_text)
    reply_text = result.reply_text
    if result.video:
        # No native rich video embed on web/app either — folded into the
        # same reply as its own line, same as before this shared core
        # existed (see app.routers.whatsapp for why WhatsApp instead sends
        # this as a separate follow-up message: it also feeds voice-reply
        # TTS synthesis there, and a spoken-aloud URL is useless).
        reply_text = f"{reply_text}\n\n📺 {result.video['title']}\n{result.video['url']}"
    return reply_text
