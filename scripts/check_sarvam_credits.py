"""
Daily probe: one-second silent clip through Sarvam speech-to-text (~₹0.01).
A non-2xx (402 = out of credits) is logged at ERROR so Sentry emails the team
before students start hitting "couldn't process that voice note".
"""
import io
import logging
import sys
import wave
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import settings  # noqa: E402
from app.logging_config import setup_logging  # noqa: E402
from app.services.sarvam_client import SARVAM_BASE_URL  # noqa: E402

logger = logging.getLogger("sarvam_probe")


def main() -> int:
    setup_logging()
    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)
    if not settings.sarvam_api_key:
        logger.error("Sarvam probe: SARVAM_API_KEY is not set")
        return 1

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)

    resp = httpx.post(
        f"{SARVAM_BASE_URL}/speech-to-text",
        headers={"api-subscription-key": settings.sarvam_api_key},
        files={"file": ("probe.wav", buf.getvalue(), "audio/wav")},
        data={"model": "saaras:v3", "language_code": "unknown"},
        timeout=30,
    )
    if resp.status_code != 200:
        logger.error(
            "Sarvam probe FAILED status=%s body=%s — voice notes and voice replies are down",
            resp.status_code, resp.text[:300],
        )
        if settings.sentry_dsn:
            import sentry_sdk

            sentry_sdk.flush(5)
        return 2
    logger.info("Sarvam probe ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
