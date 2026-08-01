from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import ChatHistory, Centre, Student
from app.services import school_billing

# A school marked "active" with no student activity for this long is a
# churn-risk signal worth a human follow-up — doesn't auto-change their
# sales_status (that's still a manual call, since a quiet week over a
# school holiday isn't the same as an actual churn).
CHURN_RISK_INACTIVE_DAYS = 21


def get_schools_overview(db: Session, organization_id: int | None = None) -> list[dict]:
    """
    Sales/pipeline view across every school — student count, school credit
    balance, and last real student activity, so staff can see at a glance
    which "active" accounts have gone quiet (see CHURN_RISK_INACTIVE_DAYS)
    without cross-referencing several pages. Used by super_admin (Qlass
    staff, sees every school) and org_admin (sees only their own
    organization's schools — pass organization_id to scope it).
    """
    now = datetime.now(timezone.utc)
    query = db.query(Centre)
    if organization_id is not None:
        query = query.filter(Centre.organization_id == organization_id)
    centres = query.order_by(Centre.id).all()

    student_counts = dict(
        db.query(Student.centre_id, func.count(Student.id))
        .filter(Student.is_staff_profile.is_(False), Student.is_deleted.is_(False))
        .group_by(Student.centre_id)
        .all()
    )
    last_activity_rows = (
        db.query(Student.centre_id, func.max(ChatHistory.created_at))
        .join(ChatHistory, ChatHistory.student_id == Student.id)
        .filter(ChatHistory.role == "user")
        .group_by(Student.centre_id)
        .all()
    )
    last_activity_by_centre = dict(last_activity_rows)

    overview = []
    for centre in centres:
        last_activity = last_activity_by_centre.get(centre.id)
        days_inactive = None
        if last_activity is not None:
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=timezone.utc)
            days_inactive = (now - last_activity).days
        is_churn_risk = (
            centre.sales_status == "active"
            and (last_activity is None or days_inactive >= CHURN_RISK_INACTIVE_DAYS)
        )
        overview.append({
            "id": centre.id,
            "name": centre.name,
            "city": centre.city,
            "sales_status": centre.sales_status,
            "sales_notes": centre.sales_notes,
            "contract_notes": centre.contract_notes,
            "pilot_status": centre.pilot_status,
            "pilot_started_at": centre.pilot_started_at,
            "pilot_expires_at": centre.pilot_expires_at,
            "student_count": student_counts.get(centre.id, 0),
            "school_credit_balance": school_billing.get_balance(db, centre.id),
            "last_activity": last_activity,
            "days_inactive": days_inactive,
            "is_churn_risk": is_churn_risk,
        })
    return overview
