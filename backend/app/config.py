import logging
from pathlib import Path

from pydantic import model_validator
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

    # OAuth 2.0 Web Client ID from Google Cloud Console (APIs & Services ->
    # Credentials) — distinct from google_api_key above (that's the Gemini
    # API key, unrelated to sign-in). Used both by the frontend (to render
    # the Google Sign-In button) and here server-side, to verify that a
    # submitted ID token was actually issued for THIS app, not some other
    # Google-integrated app (see app.services.google_auth).
    google_oauth_client_id: str | None = None

    # Wati (WhatsApp BSP) — https://live-mt-server.wati.io/<tenant_id>
    whatsapp_token: str | None = None
    wati_api_endpoint: str | None = None
    wati_webhook_secret: str | None = None
    # Required by Wati's v2 sendTemplateMessages API (undocumented until
    # now — the payload used to omit it entirely, which silently degraded
    # every template send: Wati accepted the call and returned
    # {"result": true, "error": null} but flagged every receiver as
    # isValidWhatsAppNumber: false and never actually delivered anything).
    # The sending WhatsApp Business number for this Wati account.
    wati_channel_number: str | None = None

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

    # Firebase Cloud Messaging (push notifications to the native Android
    # student app) — path to a service account JSON downloaded from the
    # Firebase console. firebase-admin is already in requirements.txt but
    # unused until this is set; see app.services.push_client.
    firebase_credentials_path: str | None = None

    # Razorpay (parent/student self-serve credit top-ups)
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None

    # Razorpay Subscriptions (recurring auto-renewal for the unlimited
    # plan) — separate from the one-time Orders API above. Plan IDs are
    # created once via the Razorpay dashboard/API (see
    # scripts/create_razorpay_plans.py) and never change; the webhook
    # secret is generated when the webhook URL is registered in the
    # Razorpay dashboard once this is deployed somewhere with a real
    # public HTTPS URL — blank until then, and this module never fills it
    # in automatically.
    razorpay_student_plan_id: str | None = None
    razorpay_teacher_plan_id: str | None = None
    razorpay_webhook_secret: str | None = None

    # Gamma (teacher-facing AI presentation generation) — Generate API is
    # beta/paid; needs a real key from https://gamma.app/api before this
    # feature can go live. See app.services.gamma_service.
    gamma_api_key: str | None = None

    # Voyage AI (text embeddings for semantic textbook retrieval) — see
    # app.services.embeddings/app.services.retrieval.fetch_semantic_candidates.
    # Free tier at https://voyageai.com. Blank until then; semantic
    # retrieval silently no-ops and the tutor falls back to the existing
    # Postgres full-text search alone (see app.services.retrieval's module
    # docstring) — never a hard failure just because this isn't set yet.
    voyage_api_key: str | None = None
    voyage_embedding_model: str = "voyage-3.5-lite"
    # Matryoshka embeddings — Voyage supports truncating to a smaller
    # dimension at request time without a separate model. 1024 is a
    # reasonable quality/storage tradeoff for short (~1000-char) textbook
    # chunks; must match the `vector(N)` column width in
    # database/migrations/0041_add_document_chunk_embeddings.sql if ever
    # changed (changing it requires re-embedding every existing chunk).
    voyage_embedding_dimensions: int = 1024

    # Where the frontend portal is actually reachable — needed server-side
    # to build a /pay link to text a parent (the backend has no notion of
    # "the current browser's origin" the way frontend code does).
    portal_base_url: str = "http://localhost:5173"
    # Comma-separated public browser origins. Keep this explicit: bearer
    # tokens must never be exposed through a wildcard CORS policy.
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"
    database_pool_size: int = 20
    database_max_overflow: int = 40

    # Lead-nurture portal integration — a separate external system that
    # sends/receives WhatsApp messages for prospective-student leads
    # through this same WhatsApp number, entirely outside the AI tutor
    # (see app.routers.leads). leads_api_key authenticates THAT portal's
    # calls to us (register a lead, send a message); leads_webhook_url/
    # secret are where WE forward an inbound message from a registered
    # lead, so their portal can drive its own nurture/nudge logic.
    leads_api_key: str | None = None
    leads_webhook_url: str | None = None
    leads_webhook_secret: str | None = None

    def cors_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.allowed_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def require_secure_production_secret(self):
        if self.environment.lower() in {"production", "staging"} and (
            self.secret_key == "changeme" or len(self.secret_key) < 32
        ):
            raise ValueError("SECRET_KEY must be at least 32 characters outside development")
        # A missing wati_webhook_secret makes app.services.whatsapp_client.
        # verify_webhook_auth accept ANY unauthenticated POST to /whatsapp/
        # webhook as if it were a real Wati-delivered message — full
        # impersonation of any student's WhatsApp identity/wallet by whoever
        # finds the URL. Deliberately a warning, not a raised error: unlike
        # secret_key (generated once, locally, before first deploy), this
        # value has to round-trip through Wati's own dashboard, so a hard
        # failure here would take down the whole backend on a routine
        # restart if that manual step hasn't happened yet. Logged instead so
        # it's loud in `journalctl`/startup logs without being an outage.
        if self.environment.lower() in {"production", "staging"} and not self.wati_webhook_secret:
            logging.getLogger(__name__).warning(
                "SECURITY WARNING: WATI_WEBHOOK_SECRET is not set — the WhatsApp webhook is accepting "
                "UNAUTHENTICATED requests and will process a POST from anyone as a real inbound message. "
                "Set a custom Authorization value in Wati's Webhook settings and WATI_WEBHOOK_SECRET here "
                "to the same value."
            )
        return self

    model_config = {"env_file": str(REPO_ROOT / ".env")}


settings = Settings()
