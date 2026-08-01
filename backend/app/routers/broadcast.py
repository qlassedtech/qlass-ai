from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core import Student, Teacher
from app.services.teacher_auth import get_current_teacher
from app.services.whatsapp_client import send_broadcast_template

router = APIRouter()

# A filter-matched broadcast has no natural upper bound the way a single
# school's pilot or bulk-upload does — a wrong/missing filter could
# accidentally target the entire platform. Wati's own bulk-send API limits
# are undocumented here (see send_broadcast_template), so this is a
# deliberately conservative safety cap: large campaigns should go out in
# batches with a human reviewing each one, not as a single accidental click.
MAX_BROADCAST_RECIPIENTS = 5000


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
async def send_broadcast(
    req: BroadcastRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    """
    Send an approved WhatsApp template to a list of students, either matched
    by filters (class/board/centre) or an explicit phone number list.
    Restricted to admin/super_admin — mass-messaging every enrolled student
    (or arbitrary phone numbers) isn't a plain teacher action.
    """
    if teacher.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can send broadcasts")

    if req.phone_numbers:
        if len(req.phone_numbers) > MAX_BROADCAST_RECIPIENTS:
            raise HTTPException(
                status_code=400,
                detail=f"Too many recipients in one broadcast (max {MAX_BROADCAST_RECIPIENTS}) — split into batches",
            )
        receivers = [{"whatsappNumber": phone, "customParams": []} for phone in req.phone_numbers]
    else:
        query = db.query(Student.phone, Student.name)
        if req.filters.class_:
            query = query.filter(Student.class_ == req.filters.class_)
        if req.filters.board:
            query = query.filter(Student.board == req.filters.board)
        # A school admin can only ever target their own school, regardless
        # of what centre_id was requested — only super_admin (Qlass staff)
        # may target a different school.
        centre_id = teacher.centre_id if teacher.role != "super_admin" else req.filters.centre_id
        if centre_id:
            query = query.filter(Student.centre_id == centre_id)
        rows = query.limit(MAX_BROADCAST_RECIPIENTS + 1).all()
        if len(rows) > MAX_BROADCAST_RECIPIENTS:
            raise HTTPException(
                status_code=400,
                detail=f"This filter matches more than {MAX_BROADCAST_RECIPIENTS} students — "
                "narrow the filters (e.g. by class/board) and send in batches",
            )
        receivers = [
            {"whatsappNumber": phone, "customParams": [{"name": "name", "value": name}]}
            for phone, name in rows
        ]

    if not receivers:
        raise HTTPException(status_code=400, detail="No matching recipients found")

    result = await send_broadcast_template(req.template_name, req.broadcast_name, receivers)
    return {"recipient_count": len(receivers), "wati_result": result}
