"""
Set, clear, or view a student's teacher-assigned focus topic — the tutor
steers toward this topic when there's a natural opening in conversation
(see the focus_topic handling in TutorAgent.build_context), without ever
ignoring what the student actually asked.

No teacher-facing UI exists yet, so this is a stopgap CLI a teacher/admin
runs directly until a real interface is built.

Usage:
    python scripts/manage_focus_topic.py set <phone> "<topic>"
    python scripts/manage_focus_topic.py clear <phone>
    python scripts/manage_focus_topic.py show <phone>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Student  # noqa: E402


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        return

    action, phone = sys.argv[1], sys.argv[2]
    db = SessionLocal()
    try:
        student = db.query(Student).filter(Student.phone == phone).first()
        if not student:
            print(f"No student found with phone {phone}")
            return

        if action == "set":
            if len(sys.argv) < 4:
                print(__doc__)
                return
            student.focus_topic = sys.argv[3]
            db.commit()
            print(f"Set focus topic for {student.name} ({phone}): {sys.argv[3]}")
        elif action == "clear":
            student.focus_topic = None
            db.commit()
            print(f"Cleared focus topic for {student.name} ({phone})")
        elif action == "show":
            print(f"{student.name} ({phone}): {student.focus_topic or '(none set)'}")
        else:
            print(__doc__)
    finally:
        db.close()


if __name__ == "__main__":
    main()
