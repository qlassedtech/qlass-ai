from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core import Student, ChatHistory

router = APIRouter()


@router.get("/{student_id}")
def get_student(student_id: int, db: Session = Depends(get_db)):
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student.as_profile_dict()


@router.get("/{student_id}/history")
def get_history(student_id: int, limit: int = 20, db: Session = Depends(get_db)):
    rows = (
        db.query(ChatHistory)
        .filter(ChatHistory.student_id == student_id)
        .order_by(ChatHistory.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {"role": r.role, "message": r.message, "created_at": r.created_at.isoformat()}
        for r in reversed(rows)
    ]
