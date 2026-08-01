"""
Create an Organization — a group of schools/centres under one umbrella
account (e.g. a state government programme spanning many schools). Create
the organization first, then either:
  - create new schools under it: python scripts/create_school.py "<name>" "<city>" --org <org_id>
  - link an existing school to it: python scripts/link_school_to_organization.py <centre_id> <org_id>
  - create an org_admin account: python scripts/create_teacher.py <phone> <name> <password> --role org_admin --org <org_id>

Usage:
    python scripts/create_organization.py "<organization name>" [--type government|school_group]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Organization  # noqa: E402


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
    org_type = _get_flag_value("--type") or "school_group"
    if org_type not in ("government", "school_group"):
        print(f"Invalid --type: {org_type!r}. Must be government or school_group.")
        return

    db = SessionLocal()
    try:
        org = Organization(name=name, org_type=org_type)
        db.add(org)
        db.commit()
        db.refresh(org)
        print(f"Created organization '{name}' (id={org.id}, type={org_type})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
