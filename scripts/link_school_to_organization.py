"""
Link an existing school (Centre) to an Organization — e.g. adding an
already-onboarded school into a state government programme's umbrella.
See scripts/create_organization.py to create the organization first.

Usage:
    python scripts/link_school_to_organization.py <centre_id> <organization_id>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Centre, Organization  # noqa: E402


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        return

    centre_id, organization_id = int(sys.argv[1]), int(sys.argv[2])

    db = SessionLocal()
    try:
        centre = db.query(Centre).filter(Centre.id == centre_id).first()
        if centre is None:
            print(f"No school with id={centre_id}")
            return
        org = db.query(Organization).filter(Organization.id == organization_id).first()
        if org is None:
            print(f"No organization with id={organization_id}")
            return
        centre.organization_id = org.id
        db.commit()
        print(f"Linked school '{centre.name}' (id={centre.id}) to organization '{org.name}' (id={org.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
