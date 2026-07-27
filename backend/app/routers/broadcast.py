from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core import Student
from app.services.whatsapp_client import send_broadcast_template

router = APIRouter()


class BroadcastFilters(BaseModel):
    class_: str | None = None
    board: str | None = None
    centre_id: int | None = None


class BroadcastRequest(BaseModel):
    template_name: str
    broadcast_name: str
    filters: BroadcastFilters = BroadcastFilters()
    phone_numbers: list[str] | None = None  # bypass filters, target these numbers directly


@router.post("/send")
async def send_broadcast(req: BroadcastRequest, db: Session = Depends(get_db)):
    """
    Send an approved WhatsApp template to a list of students, either matched
    by filters (class/board/centre) or an explicit phone number list.
    """
    if req.phone_numbers:
        receivers = [{"whatsappNumber": phone, "customParams": []} for phone in req.phone_numbers]
    else:
        query = db.query(Student)
        if req.filters.class_:
            query = query.filter(Student.class_ == req.filters.class_)
        if req.filters.board:
            query = query.filter(Student.board == req.filters.board)
        if req.filters.centre_id:
            query = query.filter(Student.centre_id == req.filters.centre_id)
        students = query.all()
        receivers = [
            {
                "whatsappNumber": s.phone,
                "customParams": [{"name": "name", "value": s.name}],
            }
            for s in students
        ]

    if not receivers:
        raise HTTPException(status_code=400, detail="No matching recipients found")

    result = await send_broadcast_template(req.template_name, req.broadcast_name, receivers)
    return {"recipient_count": len(receivers), "wati_result": result}
