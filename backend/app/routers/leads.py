import hmac
import logging

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.core import Lead
from app.services.phone import normalize_phone
from app.services.whatsapp_client import send_template_message, send_whatsapp_message

router = APIRouter()
logger = logging.getLogger(__name__)


def require_leads_api_key(authorization: str | None = Header(default=None)) -> None:
    """
    Every endpoint below is called by an external lead-nurture portal, not
    our own frontend — no student/teacher session, no CORS-scoped browser
    origin to lean on. Unlike app.services.whatsapp_client.verify_webhook_
    auth (which skips checking if unset, since a missing WATI secret must
    never take down the whole webhook on a routine restart), a missing
    LEADS_API_KEY here means the endpoint is simply disabled — these
    endpoints create real Lead rows and send real WhatsApp messages, so
    "unauthenticated" is never an acceptable default.
    """
    if not settings.leads_api_key:
        raise HTTPException(status_code=503, detail="Lead-portal integration isn't configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <key> header")
    token = authorization.removeprefix("Bearer ")
    # hmac.compare_digest (not ==) so a byte-by-byte timing side-channel
    # can't help an attacker recover the key across many requests.
    if not hmac.compare_digest(token, settings.leads_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


def _lead_to_dict(lead: Lead) -> dict:
    return {"phone": lead.phone, "name": lead.name, "external_ref": lead.external_ref}


class RegisterLeadRequest(BaseModel):
    phone: str = Field(max_length=20)
    name: str | None = Field(default=None, max_length=200)
    external_ref: str | None = Field(default=None, max_length=200)


@router.post("/leads", dependencies=[Depends(require_leads_api_key)])
def register_lead(body: RegisterLeadRequest, db: Session = Depends(get_db)):
    """
    Marks a phone number as portal-driven — from now on, an inbound
    WhatsApp message from this number is forwarded to
    settings.leads_webhook_url instead of ever reaching the AI tutor (see
    app.routers.whatsapp's early lead-routing check). Idempotent: calling
    this again for an already-registered phone just updates name/
    external_ref, same row.

    Deliberately does nothing to a phone number that's already a real
    Student or Teacher — this only ever affects genuinely new/unknown
    numbers, so registering someone who's already an active tutor student
    (by mistake, or because they later converted) can't silently cut off
    their tutor access.
    """
    phone = normalize_phone(body.phone)
    lead = db.query(Lead).filter(Lead.phone == phone).first()
    if lead is None:
        lead = Lead(phone=phone)
        db.add(lead)
    lead.name = body.name
    lead.external_ref = body.external_ref
    db.commit()
    return _lead_to_dict(lead)


@router.get("/leads", dependencies=[Depends(require_leads_api_key)])
def list_leads(db: Session = Depends(get_db)):
    return [_lead_to_dict(lead) for lead in db.query(Lead).order_by(Lead.id).all()]


@router.delete("/leads/{phone}", dependencies=[Depends(require_leads_api_key)])
def release_lead(phone: str, db: Session = Depends(get_db)):
    """
    Un-registers a lead — releases the number back to normal behavior, so
    their NEXT WhatsApp message creates a real tutor student the same way
    any other cold-start message does. The natural "this lead converted"
    action; the portal decides when that's happened, not us.
    """
    normalized = normalize_phone(phone)
    lead = db.query(Lead).filter(Lead.phone == normalized).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="No lead registered for this number")
    db.delete(lead)
    db.commit()
    return {"released": True}


class SendLeadMessageRequest(BaseModel):
    # Exactly one of these two — a plain session message only delivers
    # within WhatsApp's 24h window since the lead last messaged in; a cold
    # nurture touch (the common case for outbound-first outreach) needs an
    # approved template instead, same constraint every other outbound send
    # in this codebase already works within (see send_template_message's
    # own docstring).
    message: str | None = None
    template_name: str | None = None
    template_params: list[dict] | None = None


@router.post("/leads/{phone}/send", dependencies=[Depends(require_leads_api_key)])
async def send_lead_message(phone: str, body: SendLeadMessageRequest, db: Session = Depends(get_db)):
    normalized = normalize_phone(phone)
    if db.query(Lead).filter(Lead.phone == normalized).first() is None:
        raise HTTPException(status_code=404, detail="No lead registered for this number — register it first via POST /leads")
    if bool(body.message) == bool(body.template_name):
        raise HTTPException(status_code=400, detail="Provide exactly one of message or template_name")

    if body.template_name:
        result = await send_template_message(normalized, body.template_name, body.template_params or [])
    else:
        result = await send_whatsapp_message(normalized, body.message)

    if not result.get("sent"):
        raise HTTPException(status_code=502, detail=result.get("reason") or "Send failed")
    return {"sent": True}


async def forward_lead_message_to_portal(lead: Lead, message_text: str, raw_payload: dict) -> None:
    """
    Called from app.routers.whatsapp as soon as an inbound message from a
    registered lead is identified — BEFORE anything touches chat_core, so
    a lead's replies never enter the AI tutor pipeline at all. Best-effort:
    a webhook delivery failure to the portal must never break WhatsApp's
    own ack response, so this only logs, never raises.
    """
    if not settings.leads_webhook_url:
        logger.warning("lead message from %s could not be forwarded — LEADS_WEBHOOK_URL isn't configured", lead.phone)
        return
    headers = {}
    if settings.leads_webhook_secret:
        headers["X-Webhook-Secret"] = settings.leads_webhook_secret
    payload = {
        "phone": lead.phone, "name": lead.name, "external_ref": lead.external_ref,
        "message": message_text, "raw": raw_payload,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(settings.leads_webhook_url, json=payload, headers=headers)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("failed to forward lead message from %s to portal webhook: %s", lead.phone, exc)
