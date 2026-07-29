"""
Create a school (Centre) — the tenant boundary for this product, since it's
sold to multiple schools. Create a school first, then use
scripts/create_teacher.py with --centre <id> to add that school's staff.

Usage:
    python scripts/create_school.py "<school name>" "<city>"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Centre  # noqa: E402
from app.services import school_billing  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return

    name = sys.argv[1]
    city = sys.argv[2] if len(sys.argv) > 2 else None

    db = SessionLocal()
    try:
        centre = Centre(name=name, city=city)
        db.add(centre)
        db.commit()
        db.refresh(centre)
        school_billing.add_trial_credits(db, centre.id)
        print(f"Created school '{name}' (id={centre.id}, city={city or 'unset'})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
