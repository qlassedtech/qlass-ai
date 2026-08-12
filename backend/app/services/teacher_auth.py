from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.core import Teacher

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24 * 7  # a week — this is a low-stakes internal admin tool, not a banking app

_bearer = HTTPBearer()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_access_token(teacher_id: int, token_version: int = 0) -> str:
    payload = {
        "sub": str(teacher_id),
        "type": "teacher",  # distinguishes from a student token — see app.services.student_auth
        "tv": token_version,  # see get_current_teacher / Teacher.token_version
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


async def get_current_teacher(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer), db: Session = Depends(get_db)
) -> Teacher:
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "teacher":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
        teacher_id = int(payload["sub"])
        token_version = payload.get("tv", 0)
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    teacher = db.query(Teacher).filter(Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Teacher not found")
    # A token issued before the teacher's password was last reset carries a
    # stale "tv" — reject it rather than trust a 7-day-old credential that
    # was supposed to have been invalidated (see Teacher.token_version and
    # /auth/reset-password). Tokens minted before this field existed have no
    # "tv" claim at all (defaults to 0 above), which matches every existing
    # teacher's token_version default (also 0), so nobody is logged out by
    # this change landing.
    if token_version != (teacher.token_version or 0):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return teacher
