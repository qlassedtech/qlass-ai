import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FILE = LOG_DIR / "app.log"

_PHONE_RE = re.compile(r"\d{10,}")
_SECRET_RE = re.compile(r"(?i)((?:api[_-]?key|key|token|secret|password|authorization)\s*[=:]\s*)(\S+)")


def redact(text: str) -> str:
    text = _PHONE_RE.sub("[phone]", text)
    return _SECRET_RE.sub(r"\1[redacted]", text)


class RedactingFilter(logging.Filter):
    """Masks phone-number-length digit runs and key/token values in every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def setup_logging() -> None:
    """
    Replaces scattered print() calls with a real logger: rotated file (so a
    long-running server doesn't grow one unbounded log file) plus console
    output, so failures are visible both live and after the fact.
    """
    LOG_DIR.mkdir(exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return  # already configured (e.g. reloader re-import)

    root.setLevel(logging.INFO)
    # httpx/httpcore log every request URL at INFO — includes phone numbers in Wati paths.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(RedactingFilter())
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(RedactingFilter())
    root.addHandler(console_handler)
