from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.core import Student
from app.services.teacher_auth import JWT_ALGORITHM, JWT_EXPIRY_HOURS

_bearer = HTTPBearer()


def create_student_access_token(student_id: int, token_version: int = 0) -> str:
    payload = {
        "sub": str(student_id),
        "tv": token_version,  # see Student.token_version
        "type": "student",  # distinguishes from a teacher token — see app.services.teacher_auth
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def get_student_by_token(token: str, db: Session) -> Student | None:
    """
    Decode+lookup shared by both the normal REST dependency below (which
    gets the token from the Authorization header) and the voice-call
    WebSocket endpoint (app.routers.voice_call), which gets it from a
    `?token=` query param instead — a browser WebSocket handshake can't set
    a custom Authorization header, so that endpoint can't use the
    HTTPBearer-based dependency directly, but must still apply exactly the
    same validation (signature, token type, expiry, token_version). Returns
    None rather than raising, since a WebSocket close/error frame is sent
    differently than an HTTP 401.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "student":
            return None
        student_id = int(payload["sub"])
        token_version = payload.get("tv", 0)
    except (jwt.PyJWTError, KeyError, ValueError):
        return None

    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        return None
    if token_version != (student.token_version or 0):
        return None
    return student


async def get_current_student(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer), db: Session = Depends(get_db)
) -> Student:
    student = get_student_by_token(credentials.credentials, db)
    if not student:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return student
