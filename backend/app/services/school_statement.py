import calendar
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import Centre, SchoolCreditEvent
from app.services.pdf_render import render_school_statement_pdf


def _period_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = calendar.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    return start, end


def generate_school_statement_pdf(db: Session, centre: Centre, year: int, month: int) -> bytes:
    """
    Builds the monthly school-level billing statement (workbook/presentation
    generation spend, on the shared school_credit_events ledger — see
    app.services.school_billing) for the given calendar month.
    """
    start, end = _period_bounds(year, month)

    opening_balance = float(
        db.query(func.coalesce(func.sum(SchoolCreditEvent.amount), 0))
        .filter(SchoolCreditEvent.centre_id == centre.id, SchoolCreditEvent.created_at < start)
        .scalar()
    )
    period_events = (
        db.query(SchoolCreditEvent)
        .filter(SchoolCreditEvent.centre_id == centre.id, SchoolCreditEvent.created_at >= start, SchoolCreditEvent.created_at <= end)
        .all()
    )
    total_topped_up = float(sum(e.amount for e in period_events if e.amount > 0))
    total_spent = float(sum(-e.amount for e in period_events if e.amount < 0))
    closing_balance = opening_balance + total_topped_up - total_spent

    spend_by_service: dict[str, float] = {}
    for e in period_events:
        if e.amount < 0:
            spend_by_service[e.service or "other"] = spend_by_service.get(e.service or "other", 0.0) + float(-e.amount)
    spend_rows = sorted(spend_by_service.items(), key=lambda kv: kv[1], reverse=True)

    period_label = f"{calendar.month_name[month]} {year}"
    return render_school_statement_pdf(
        school_name=centre.name,
        school_logo_url=centre.logo_url,
        period_label=period_label,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        total_topped_up=total_topped_up,
        total_spent=total_spent,
        spend_by_service=spend_rows,
    )
