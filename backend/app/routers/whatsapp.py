from fastapi import APIRouter, Request, Query, HTTPException, Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.core import Student, ChatHistory
from app.services.whatsapp_client import parse_incoming_message, send_whatsapp_message
from app.agents.tutor_agent import TutorAgent

router = APIRouter()
tutor_agent = TutorAgent()


@router.get("/webhook")
def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    """Meta calls this once to verify the webhook URL (Step 9)."""
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_message(request: Request, db: Session = Depends(get_db)):
    """
    Full working flow:
    WhatsApp -> parse -> find/create Student -> TutorAgent (calls LLM)
    -> save chat_history -> send reply back via WhatsApp Cloud API
    """
    payload = await request.json()
    parsed = parse_incoming_message(payload)
    if not parsed:
        return {"received": True, "handled": False}

    from_phone, message_text = parsed
    if not message_text:
        return {"received": True, "handled": False, "reason": "no text body"}

    # Find or create the student profile (Phase 3/4)
    student = db.query(Student).filter(Student.phone == from_phone).first()
    if student is None:
        student = Student(name="New Student", phone=from_phone)
        db.add(student)
        db.commit()
        db.refresh(student)

    # Save the incoming message
    db.add(ChatHistory(student_id=student.id, role="user", message=message_text, agent="tutor"))
    db.commit()

    # Route to the Tutor Agent -> real LLM call
    reply_text = await tutor_agent.respond(student.as_profile_dict(), message_text)

    # Save the reply
    db.add(ChatHistory(student_id=student.id, role="assistant", message=reply_text, agent="tutor"))
    db.commit()

    # Send back over WhatsApp
    send_result = await send_whatsapp_message(from_phone, reply_text)

    return {"received": True, "handled": True, "reply": reply_text, "whatsapp_send": send_result}
