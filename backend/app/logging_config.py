import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FILE = LOG_DIR / "app.log"


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
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)
