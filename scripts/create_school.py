"""
Create a school (Centre) — the tenant boundary for this product, since it's
sold to multiple schools. Create a school first, then use
scripts/create_teacher.py with --centre <id> to add that school's staff.

Pass --org <organization_id> to place it directly under an Organization
(e.g. a state government programme spanning many schools) — see
scripts/create_organization.py.

Usage:
    python scripts/create_school.py "<school name>" "<city>" [--org <organization_id>]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Centre  # noqa: E402
from app.services import school_billing  # noqa: E402


def _get_flag_value(flag: str) -> str | None:
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return

    name = sys.argv[1]
    city = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
    org_id = _get_flag_value("--org")

    db = SessionLocal()
    try:
        centre = Centre(name=name, city=city, organization_id=int(org_id) if org_id else None)
        db.add(centre)
        db.commit()
        db.refresh(centre)
        school_billing.add_trial_credits(db, centre.id)
        print(f"Created school '{name}' (id={centre.id}, city={city or 'unset'}, organization_id={org_id or 'none'})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
