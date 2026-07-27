from fastapi import FastAPI
from app.routers import whatsapp, students, health, broadcast
from app.database import Base, engine
from app.config import settings
import app.models.core  # noqa: F401 - registers models on Base

app = FastAPI(title="Qlass AI OS", version="0.1.0")

app.include_router(health.router)
app.include_router(whatsapp.router, prefix="/whatsapp", tags=["whatsapp"])
app.include_router(students.router, prefix="/students", tags=["students"])
app.include_router(broadcast.router, prefix="/broadcast", tags=["broadcast"])


@app.on_event("startup")
def on_startup():
    # Convenience for local dev only. In staging/prod, use Alembic migrations
    # (database/migrations/) against database/schema.sql instead.
    if settings.environment == "development":
        Base.metadata.create_all(bind=engine)


@app.get("/")
def root():
    return {"service": "Qlass AI OS", "status": "running"}
