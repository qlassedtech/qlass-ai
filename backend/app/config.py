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
    sarvam_tts_speaker: str = "priya"

    # Azure AI Vision (OCR for image-based homework/question photos)
    azure_vision_endpoint: str | None = None
    azure_vision_key: str | None = None

    # Azure OpenAI (gpt-image series — DALL-E 3 was retired March 2026)
    azure_image_endpoint: str | None = None
    azure_image_key: str | None = None
    azure_image_deployment: str | None = None
    azure_image_api_version: str = "2025-04-01-preview"

    # YouTube Data API v3 (best-matching video suggestion for a topic)
    youtube_api_key: str | None = None

    # Razorpay (parent/student self-serve credit top-ups)
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None

    # Gamma (teacher-facing AI presentation generation) — Generate API is
    # beta/paid; needs a real key from https://gamma.app/api before this
    # feature can go live. See app.services.gamma_service.
    gamma_api_key: str | None = None

    # Where the frontend portal is actually reachable — needed server-side
    # to build a /pay link to text a parent (the backend has no notion of
    # "the current browser's origin" the way frontend code does).
    portal_base_url: str = "http://localhost:5173"

    class Config:
        env_file = str(REPO_ROOT / ".env")


settings = Settings()
