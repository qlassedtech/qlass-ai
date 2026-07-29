from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import ChatHistory, CreditEvent, SchoolCreditEvent, Student, TopicProgress
from app.services.escalation import ESCALATION_THRESHOLD

WEAK_TOPIC_LIMIT = 5
INACTIVE_STUDENT_LIMIT = 10
INACTIVE_THRESHOLD_DAYS = 7

# A student is "at risk" (falling behind, distinct from "inactive" above —
# this is about struggling while still engaged) if either their recent
# accuracy is poor on a real sample, or they're currently mid-struggle
# (one hint-only turn away from actually triggering app.services.escalation).
STRUGGLING_ACCURACY_THRESHOLD_PCT = 50
MIN_EVALUATED_FOR_RISK = 3
AT_RISK_STUDENT_LIMIT = 10

# Mirrors whatsapp.MONTHLY_STUDENT_CREDIT_LIMIT — duplicated rather than
# imported since that constant lives in a router module and analytics is a
# service (importing router->service->router would be circular).
MONTHLY_STUDENT_CREDIT_LIMIT = 100.0
UPSELL_CANDIDATE_LIMIT = 10


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def get_school_analytics(db: Session, student_ids: list[int], centre_id: int | None) -> dict:
    """
    Aggregate, school-wide view for the admin dashboard — everything here
    is computed from the SAME tables the per-student views already use
    (TopicProgress, ChatHistory, CreditEvent), just rolled up across every
    real student in scope (is_staff_profile already excluded by the
    caller's student_ids list).
    """
    if not student_ids:
        return {
            "total_students": 0, "active_this_week": 0, "inactive_students": [],
            "avg_accuracy_pct": None, "top_weak_topics": [], "total_credit_spend_this_month": 0.0,
            "workbook_generations_this_month": 0, "presentation_generations_this_month": 0,
            "upsell_candidates": [], "at_risk_students": [],
        }

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    active_this_week = (
        db.query(func.count(func.distinct(ChatHistory.student_id)))
        .filter(ChatHistory.student_id.in_(student_ids), ChatHistory.role == "user", ChatHistory.created_at >= week_ago)
        .scalar()
    )

    # Last-message date per student, to find who's gone quiet.
    last_message_rows = (
        db.query(ChatHistory.student_id, func.max(ChatHistory.created_at))
        .filter(ChatHistory.student_id.in_(student_ids), ChatHistory.role == "user")
        .group_by(ChatHistory.student_id)
        .all()
    )
    last_message_by_student = {student_id: last_at for student_id, last_at in last_message_rows}

    now = datetime.now(timezone.utc)
    inactive = []
    students_by_id: dict[int, Student] = {}
    for student in db.query(Student).filter(Student.id.in_(student_ids)).all():
        students_by_id[student.id] = student
        last_at = last_message_by_student.get(student.id)
        if last_at is None:
            days_since = None
        else:
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=timezone.utc)
            days_since = (now - last_at).days
        if last_at is None or days_since >= INACTIVE_THRESHOLD_DAYS:
            inactive.append({"id": student.id, "name": student.name, "phone": student.phone, "days_since_last_message": days_since})
    inactive.sort(key=lambda s: (s["days_since_last_message"] is not None, s["days_since_last_message"] or 0), reverse=True)
    inactive = inactive[:INACTIVE_STUDENT_LIMIT]

    # Per-student accuracy, averaged (not a single school-wide correct/total
    # ratio, so a highly-active student doesn't dominate the average).
    topic_rows = db.query(TopicProgress).filter(TopicProgress.student_id.in_(student_ids)).all()
    per_student_topics: dict[int, dict[str, int]] = {}
    topic_tally: dict[str, dict[str, int]] = {}
    for row in topic_rows:
        s = per_student_topics.setdefault(row.student_id, {"correct": 0, "incorrect": 0})
        t = topic_tally.setdefault(row.topic, {"correct": 0, "incorrect": 0})
        if row.is_correct is True:
            s["correct"] += 1
            t["correct"] += 1
        elif row.is_correct is False:
            s["incorrect"] += 1
            t["incorrect"] += 1

    accuracies = []
    for counts in per_student_topics.values():
        total = counts["correct"] + counts["incorrect"]
        if total:
            accuracies.append(counts["correct"] / total * 100)
    avg_accuracy_pct = round(sum(accuracies) / len(accuracies)) if accuracies else None

    at_risk_by_id: dict[int, dict] = {}
    for student_id, counts in per_student_topics.items():
        total = counts["correct"] + counts["incorrect"]
        if total < MIN_EVALUATED_FOR_RISK:
            continue
        accuracy_pct = round(counts["correct"] / total * 100)
        if accuracy_pct < STRUGGLING_ACCURACY_THRESHOLD_PCT:
            student = students_by_id.get(student_id)
            if student is not None:
                at_risk_by_id[student_id] = {
                    "id": student.id, "name": student.name, "phone": student.phone,
                    "accuracy_pct": accuracy_pct, "consecutive_unresolved_hints": student.consecutive_unresolved_hints or 0,
                }
    for student in students_by_id.values():
        hint_streak = student.consecutive_unresolved_hints or 0
        if hint_streak >= ESCALATION_THRESHOLD - 1 and student.id not in at_risk_by_id:
            counts = per_student_topics.get(student.id)
            total = (counts["correct"] + counts["incorrect"]) if counts else 0
            accuracy_pct = round(counts["correct"] / total * 100) if total else None
            at_risk_by_id[student.id] = {
                "id": student.id, "name": student.name, "phone": student.phone,
                "accuracy_pct": accuracy_pct, "consecutive_unresolved_hints": hint_streak,
            }
    at_risk_students = sorted(
        at_risk_by_id.values(),
        key=lambda s: (-s["consecutive_unresolved_hints"], s["accuracy_pct"] if s["accuracy_pct"] is not None else 999),
    )[:AT_RISK_STUDENT_LIMIT]

    weak_topics = sorted(
        ((topic, c["incorrect"]) for topic, c in topic_tally.items() if c["incorrect"] > c["correct"]),
        key=lambda x: x[1], reverse=True,
    )[:WEAK_TOPIC_LIMIT]

    month_start = _month_start()
    total_spend = (
        db.query(func.coalesce(func.sum(-CreditEvent.amount), 0))
        .filter(CreditEvent.student_id.in_(student_ids), CreditEvent.amount < 0, CreditEvent.created_at >= month_start)
        .scalar()
    )

    # Students who've hit (or nearly hit) their monthly ₹ cap are the
    # clearest usage-based signal that the flat-fee unlimited plan (see
    # cost_tracker._is_unlimited_active) would suit them better than
    # topping up piecemeal — surfaced here so a teacher/admin can pitch it.
    spend_rows = (
        db.query(CreditEvent.student_id, func.sum(-CreditEvent.amount))
        .filter(CreditEvent.student_id.in_(student_ids), CreditEvent.amount < 0, CreditEvent.created_at >= month_start)
        .group_by(CreditEvent.student_id)
        .having(func.sum(-CreditEvent.amount) >= MONTHLY_STUDENT_CREDIT_LIMIT)
        .all()
    )
    spend_by_student = {student_id: float(spend) for student_id, spend in spend_rows}
    upsell_candidates = []
    if spend_by_student:
        students_by_id = {s.id: s for s in db.query(Student).filter(Student.id.in_(spend_by_student.keys())).all()}
        for student_id, spend in spend_by_student.items():
            student = students_by_id.get(student_id)
            if student is None or student.subscription_plan == "unlimited":
                continue  # already on the flat-fee plan, or a race with a since-deleted student
            upsell_candidates.append({"id": student.id, "name": student.name, "phone": student.phone, "spend_this_month": spend})
        upsell_candidates.sort(key=lambda s: s["spend_this_month"], reverse=True)
        upsell_candidates = upsell_candidates[:UPSELL_CANDIDATE_LIMIT]

    workbook_count = presentation_count = 0
    if centre_id is not None:
        workbook_count = (
            db.query(func.count(SchoolCreditEvent.id))
            .filter(SchoolCreditEvent.centre_id == centre_id, SchoolCreditEvent.service == "workbook_pdf",
                     SchoolCreditEvent.created_at >= month_start)
            .scalar()
        )
        presentation_count = (
            db.query(func.count(SchoolCreditEvent.id))
            .filter(SchoolCreditEvent.centre_id == centre_id, SchoolCreditEvent.service == "gamma_presentation",
                     SchoolCreditEvent.created_at >= month_start)
            .scalar()
        )

    return {
        "total_students": len(student_ids),
        "active_this_week": active_this_week or 0,
        "inactive_students": inactive,
        "avg_accuracy_pct": avg_accuracy_pct,
        "top_weak_topics": [{"topic": t, "incorrect_count": c} for t, c in weak_topics],
        "total_credit_spend_this_month": float(total_spend),
        "workbook_generations_this_month": workbook_count or 0,
        "presentation_generations_this_month": presentation_count or 0,
        "upsell_candidates": upsell_candidates,
        "at_risk_students": at_risk_students,
    }
