import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import REPO_ROOT

UPLOAD_ROOT = REPO_ROOT / "backend" / "static" / "uploads"
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB — plenty for a logo/profile photo, small enough to not bloat storage


async def save_image_upload(file: UploadFile, subfolder: str) -> str:
    """
    Saves an uploaded image under static/uploads/<subfolder>/ with a random
    filename (avoids collisions and path traversal from the original
    filename) and returns the URL path to store on the owning row (Centre.
    logo_url / Student.photo_url / Teacher.photo_url).
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only JPG, PNG, or WEBP images are allowed")

    contents = await file.read()
    if len(contents) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image must be under 5MB")

    folder = UPLOAD_ROOT / subfolder
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    (folder / filename).write_bytes(contents)

    return f"/static/uploads/{subfolder}/{filename}"
