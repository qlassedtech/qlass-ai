"""POST /student-app/auth/login applies the same failed-attempt lockout the teacher login does (audit Sept 2026, H3)."""
import uuid

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models.core import Centre, Student
from app.services.rate_limit import LOGIN_FAIL_MAX_PER_IP
from app.services.teacher_auth import hash_password


def _student_with_password(db_session) -> Student:
    centre = Centre(name="Lockout School")
    db_session.add(centre)
    db_session.commit()
    # Unique per run: the limiter is backed by real Redis when reachable, with a 10-minute window.
    student = Student(
        name="Locked Out", phone=f"919{uuid.uuid4().int % 10**9:09d}", centre_id=centre.id,
        password_hash=hash_password("correct-horse"),
    )
    db_session.add(student)
    db_session.commit()
    return student


def test_repeated_wrong_passwords_lock_the_account_even_for_the_right_password(db_session):
    student = _student_with_password(db_session)
    app.dependency_overrides[get_db] = lambda: (yield db_session)
    client = TestClient(app)
    try:
        # A correct login works before any failures...
        ok = client.post("/student-app/auth/login", json={"phone": student.phone, "password": "correct-horse"})
        assert ok.status_code == 200 and ok.json()["access_token"]

        for _ in range(LOGIN_FAIL_MAX_PER_IP + 1):
            wrong = client.post("/student-app/auth/login", json={"phone": student.phone, "password": "nope"})
            assert wrong.status_code in (401, 429)

        # ...but after 6 wrong attempts the right password is blocked too.
        blocked = client.post("/student-app/auth/login", json={"phone": student.phone, "password": "correct-horse"})
        assert blocked.status_code == 429
        assert "Too many login attempts" in blocked.json()["detail"]
    finally:
        app.dependency_overrides.clear()
