import asyncio
import importlib
import math

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from app.routers import whatsapp, health, broadcast, admin, payments, student_app, parent_app, razorpay_webhook, public
from app.database import Base, engine
from app.config import settings, REPO_ROOT
from app.logging_config import setup_logging

# Pure side-effect import (registers models on Base) — done via importlib
# rather than `import app.models.core` so it doesn't bind a top-level name
# `app` that the very next line then immediately shadows with the FastAPI
# instance. That shadowing was harmless today (nothing below references
# the module), but it's a real trap: anyone later writing `app.models.core`
# expecting the module would silently resolve to the FastAPI app instead.
importlib.import_module("app.models.core")

setup_logging()

app = FastAPI(title="Qlass AI OS", version="0.1.0")

# Uploaded school logos / student & teacher photos (see app.services.uploads)
_static_dir = REPO_ROOT / "backend" / "static"
_static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# The admin/teacher portal (frontend/) and the student web app
# (student-frontend/) run as separate Vite dev servers on different
# origins — restricted to known local dev ports rather than a wildcard,
# since this API issues auth tokens.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(whatsapp.router, prefix="/whatsapp", tags=["whatsapp"])
app.include_router(broadcast.router, prefix="/broadcast", tags=["broadcast"])
app.include_router(admin.router, tags=["admin"])
app.include_router(payments.router, tags=["payments"])
app.include_router(student_app.router, tags=["student-app"])
app.include_router(parent_app.router, tags=["parent-app"])
app.include_router(razorpay_webhook.router, tags=["razorpay-webhook"])
app.include_router(public.router, tags=["public"])


def _sanitize_non_finite(value):
    """Replace inf/-inf/NaN with a plain string, recursively — see the
    exception handler below for why."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _sanitize_non_finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_non_finite(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    """
    Confirmed live: a request body containing a non-finite float
    (Infinity/-Infinity/NaN — non-standard JSON tokens that Python's own
    json.loads still accepts) correctly FAILS Pydantic validation, but
    FastAPI's default error handler then crashes trying to render the
    response — it echoes the rejected raw value back for debugging
    (errors()[i]["input"]), and Starlette's JSONResponse explicitly sets
    allow_nan=False, so json.dumps raises ValueError mid-response. The net
    effect was a correctly-validated 422 turning into an unhandled 500.
    This mirrors FastAPI's own default handler exactly, except it
    sanitizes non-finite floats out of the echoed input first.
    """
    return JSONResponse(status_code=422, content={"detail": _sanitize_non_finite(exc.errors())})


@app.on_event("startup")
async def on_startup():
    # Convenience for local dev only. In staging/prod, use Alembic migrations
    # (database/migrations/) against database/schema.sql instead.
    if settings.environment == "development":
        Base.metadata.create_all(bind=engine)
    app.state.webhook_retry_task = asyncio.create_task(whatsapp.retry_pending_webhooks())


@app.on_event("shutdown")
async def on_shutdown():
    task = getattr(app.state, "webhook_retry_task", None)
    if task:
        task.cancel()


@app.get("/")
def root():
    return {"service": "Qlass AI OS", "status": "running"}
