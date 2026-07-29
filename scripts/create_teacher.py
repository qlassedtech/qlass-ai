"""
Create a teacher/admin/super_admin account for the web portal — there's no
self-signup flow (this is an internal tool), so accounts are provisioned
here. Every account except super_admin must belong to a school (centre) —
this product is sold to multiple schools, and centre_id is what keeps one
school's students/teachers isolated from another's (see app/routers/admin.py).

Usage:
    python scripts/create_teacher.py <phone> <name> <password> --role teacher --centre <centre_id>
    python scripts/create_teacher.py <phone> <name> <password> --role admin --centre <centre_id>
    python scripts/create_teacher.py <phone> <name> <password> --role super_admin

Use scripts/create_school.py first to create a centre_id if you don't have one yet.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Teacher  # noqa: E402
from app.services.teacher_auth import hash_password  # noqa: E402


def _get_flag_value(flag: str) -> str | None:
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


def main() -> None:
    if len(sys.argv) < 4:
        print(__doc__)
        return

    phone, name, password = sys.argv[1], sys.argv[2], sys.argv[3]
    role = _get_flag_value("--role") or "teacher"
    if role not in ("teacher", "admin", "super_admin"):
        print(f"Invalid role: {role!r}. Must be teacher, admin, or super_admin.")
        return

    centre_id = _get_flag_value("--centre")
    if role != "super_admin" and not centre_id:
        print("A --centre <id> is required for teacher/admin roles (only super_admin can omit it).")
        return
    centre_id = int(centre_id) if centre_id else None

    db = SessionLocal()
    try:
        existing = db.query(Teacher).filter(Teacher.phone == phone).first()
        if existing:
            existing.password_hash = hash_password(password)
            existing.name = name
            existing.role = role
            existing.centre_id = centre_id
            db.commit()
            print(f"Updated existing teacher {name} ({phone}), role={role}, centre_id={centre_id}")
        else:
            teacher = Teacher(name=name, phone=phone, password_hash=hash_password(password), role=role, centre_id=centre_id)
            db.add(teacher)
            db.commit()
            print(f"Created teacher {name} ({phone}), role={role}, centre_id={centre_id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
