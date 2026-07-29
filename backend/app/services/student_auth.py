from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.core import Student
from app.services.teacher_auth import JWT_ALGORITHM, JWT_EXPIRY_HOURS

_bearer = HTTPBearer()


def create_student_access_token(student_id: int) -> str:
    payload = {
        "sub": str(student_id),
        "type": "student",  # distinguishes from a teacher token — see app.services.teacher_auth
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_student(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer), db: Session = Depends(_get_db)
) -> Student:
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "student":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
        student_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Student not found")
    return student
