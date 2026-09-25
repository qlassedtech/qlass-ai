import logging
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.core import Student
from app.services.rate_limit import claim_single_use_token
from app.services.teacher_auth import JWT_ALGORITHM, JWT_EXPIRY_HOURS

logger = logging.getLogger(__name__)

_bearer = HTTPBearer()

# The voice-call WebSocket (app.routers.voice_call) can't send an
# Authorization header from a browser, so its credential has to travel in
# the URL — and URLs get logged (nginx access logs, browser history,
# Referer). The 7-day student JWT used to be that credential (audit, Sept
# 2026: H1). It's now a separate short-lived, single-use ticket, minted by
# POST /student-app/voice-call/ticket against the normal bearer auth and
# consumed on the WebSocket handshake — so a leaked URL is worth 60
# seconds, once, instead of a week of full account access.
VOICE_TICKET_TTL_SECONDS = 60
# The used-jti record must outlive the ticket's own expiry so a replay
# right at the edge of the window is still caught.
VOICE_TICKET_USED_TTL_SECONDS = 120
VOICE_TICKET_TYPE = "voice_ticket"


def create_student_access_token(student_id: int, token_version: int = 0) -> str:
    payload = {
        "sub": str(student_id),
        "tv": token_version,  # see Student.token_version
        "type": "student",  # distinguishes from a teacher token — see app.services.teacher_auth
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def _load_student_for_payload(payload: dict, db: Session) -> Student | None:
    """Shared tail of every student-token validation: row lookup + token_version check."""
    try:
        student_id = int(payload["sub"])
        token_version = payload.get("tv", 0)
    except (KeyError, ValueError, TypeError):
        return None
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        return None
    if token_version != (student.token_version or 0):
        return None
    return student


def get_student_by_token(token: str, db: Session) -> Student | None:
    """
    Decode+lookup behind the normal REST dependency below (which gets the
    token from the Authorization header). Applies signature, token type,
    expiry and token_version checks. Returns None rather than raising so
    callers that aren't plain HTTP handlers can decide how to report it.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "student":
        return None
    return _load_student_for_payload(payload, db)


def create_voice_call_ticket(student: Student, ttl_seconds: int = VOICE_TICKET_TTL_SECONDS) -> str:
    """
    Mint a short-lived, single-use ticket for the voice-call WebSocket —
    see VOICE_TICKET_TTL_SECONDS. Carries the same `tv` (token_version) as
    a normal student token so a logout-everywhere still invalidates a
    ticket minted just before it. `ttl_seconds` is a parameter only so
    tests can mint an already-expired ticket.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(student.id),
        "tv": student.token_version or 0,
        "type": VOICE_TICKET_TYPE,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


async def consume_voice_call_ticket(ticket: str, db: Session) -> Student | None:
    """
    Validate and burn a voice-call ticket: signature, type, expiry, then a
    single-use claim on its jti (Redis SETNX; per-process fallback when
    Redis is unreachable — see rate_limit.claim_single_use_token), then the
    exact same student lookup/token_version check a normal token gets.
    Returns None on any failure — the WebSocket caller sends its own error
    frame and closes.
    """
    try:
        payload = jwt.decode(ticket, settings.secret_key, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != VOICE_TICKET_TYPE:
        return None
    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        return None
    if not await claim_single_use_token(f"voice_ticket:{jti}", VOICE_TICKET_USED_TTL_SECONDS):
        logger.warning("voice-call ticket reuse rejected (jti=%s)", jti[:8])
        return None
    return _load_student_for_payload(payload, db)


async def get_current_student(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer), db: Session = Depends(get_db)
) -> Student:
    student = get_student_by_token(credentials.credentials, db)
    if not student:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return student
