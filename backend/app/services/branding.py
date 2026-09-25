"""
Skoolgpt branding for generated media — every visual the product hands a
student (generated diagrams, mind maps, PDFs) carries the logo.
"""
import logging
from io import BytesIO

from app.config import REPO_ROOT

logger = logging.getLogger(__name__)

LOGO_PATH = REPO_ROOT / "backend" / "static" / "brand" / "logo-tight.png"


def stamp_logo(
    png_bytes: bytes, *, width_ratio: float = 0.14, margin_ratio: float = 0.02, opacity: float = 0.92,
) -> bytes:
    """
    Pastes the Skoolgpt logo, scaled to `width_ratio` of the image's width,
    in the bottom-right corner with a `margin_ratio` margin, and returns
    PNG bytes of the same dimensions. On ANY failure (missing logo file,
    undecodable input, ...) the original bytes are returned unchanged and
    a warning is logged — stamping must never break delivery.
    """
    try:
        from PIL import Image

        with Image.open(BytesIO(png_bytes)) as source:
            image = source.convert("RGBA")
        with Image.open(LOGO_PATH) as logo_file:
            logo = logo_file.convert("RGBA")

        target_w = max(1, int(round(image.width * width_ratio)))
        target_h = max(1, int(round(logo.height * target_w / logo.width)))
        logo = logo.resize((target_w, target_h), Image.LANCZOS)
        if opacity < 1:
            alpha = logo.getchannel("A").point(lambda a: int(a * opacity))
            logo.putalpha(alpha)

        margin = int(round(image.width * margin_ratio))
        position = (image.width - target_w - margin, image.height - target_h - margin)
        image.alpha_composite(logo, dest=(max(position[0], 0), max(position[1], 0)))

        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        logger.warning("stamp_logo: could not stamp the logo; delivering the image unstamped", exc_info=True)
        return png_bytes
