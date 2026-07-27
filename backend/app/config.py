from pathlib import Path

from pydantic_settings import BaseSettings

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    environment: str = "development"
    secret_key: str = "changeme"

    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    google_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Wati (WhatsApp BSP) — https://live-mt-server.wati.io/<tenant_id>
    whatsapp_token: str | None = None
    wati_api_endpoint: str | None = None
    wati_webhook_secret: str | None = None

    google_service_account_json: str | None = None
    google_drive_root_folder_id: str | None = None

    chroma_persist_dir: str = "./rag/chroma_store"

    # Sarvam (Indic speech-to-text / text-to-speech) — Voice Tutor (Phase 11)
    sarvam_api_key: str | None = None
    sarvam_language_code: str = "en-IN"
    sarvam_tts_speaker: str = "shubh"

    class Config:
        env_file = str(REPO_ROOT / ".env")


settings = Settings()
