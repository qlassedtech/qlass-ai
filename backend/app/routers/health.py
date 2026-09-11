import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core import ProcessedWebhookMessage
from app.services import rate_limit

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health_check():
    return {"status": "ok"}


@router.get("/ready")
async def readiness_check(db: Session = Depends(get_db)):
    """Load-balancer readiness: 503 unless both Postgres and Redis are usable."""
    db_ok = redis_ok = False
    pending_jobs = None
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
        pending_jobs = (
            db.query(ProcessedWebhookMessage).filter(ProcessedWebhookMessage.status == "pending").count()
        )
    except Exception as exc:
        logger.warning("readiness: database check failed: %s", exc)
    try:
        if rate_limit._redis is not None:
            await rate_limit._redis.ping()
            redis_ok = True
    except Exception as exc:
        logger.warning("readiness: redis check failed: %s", exc)
    body = {"status": "ready" if db_ok and redis_ok else "unavailable", "db": db_ok, "redis": redis_ok, "pending_jobs": pending_jobs}
    return JSONResponse(status_code=200 if db_ok and redis_ok else 503, content=body)
