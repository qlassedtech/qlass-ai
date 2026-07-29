from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.core import Parent
from app.services.teacher_auth import JWT_ALGORITHM, JWT_EXPIRY_HOURS

_bearer = HTTPBearer()


def create_parent_access_token(parent_id: int) -> str:
    payload = {
        "sub": str(parent_id),
        "type": "parent",  # its own type, distinct from "teacher"/"student" — see the audit fix in those modules
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_parent(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer), db: Session = Depends(_get_db)
) -> Parent:
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "parent":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
        parent_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    parent = db.query(Parent).filter(Parent.id == parent_id).first()
    if not parent:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Parent account not found")
    return parent
