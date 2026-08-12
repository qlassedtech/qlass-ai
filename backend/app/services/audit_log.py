from sqlalchemy.orm import Session

from app.models.core import AuditLog


def record(db: Session, actor_teacher_id: int, action: str, target_type: str, target_id: int, detail: str | None = None) -> None:
    """
    Writes one audit-trail row for a high-blast-radius admin action (credit
    grants, subscription activation, destructive deletion — see
    AuditLog's docstring for why this exists). Deliberately its own commit
    (not folded into the caller's next db.commit()) so the audit row lands
    even if something later in the same request fails and rolls back the
    rest — an audit log that can silently disappear alongside the action
    it was meant to record isn't a real audit log.
    """
    db.add(AuditLog(actor_teacher_id=actor_teacher_id, action=action, target_type=target_type, target_id=target_id, detail=detail))
    db.commit()
