from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.routers import whatsapp, health, broadcast, admin, payments, student_app, parent_app
from app.database import Base, engine
from app.config import settings, REPO_ROOT
from app.logging_config import setup_logging
import app.models.core  # noqa: F401 - registers models on Base

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
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5174", "http://127.0.0.1:5174",
    ],
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


@app.on_event("startup")
def on_startup():
    # Convenience for local dev only. In staging/prod, use Alembic migrations
    # (database/migrations/) against database/schema.sql instead.
    if settings.environment == "development":
        Base.metadata.create_all(bind=engine)


@app.get("/")
def root():
    return {"service": "Qlass AI OS", "status": "running"}
