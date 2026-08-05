"""
Regression test for a real bug found during the multi-tenancy/scale audit:
_student_to_dict issued 3 queries PER STUDENT (parent lookup, wallet
balance sum, referral-credit sum) — confirmed live against a 500-student
seeded database, a single page load took ~1500 queries and 1.5-2.3s.
list_students now batch-fetches all three in 3 queries total regardless of
page size; this test locks that in via a query count that must stay flat
as student count grows, not just check the response looks right.
"""
from sqlalchemy import event

from app.models.core import Centre, CreditEvent, Parent, Student, Teacher
from app.routers.admin import list_students
from app.services import cost_tracker


def _make_school(db_session, name="Test School"):
    centre = Centre(name=name)
    db_session.add(centre)
    db_session.commit()
    return centre


def _count_queries(db_session, fn):
    count = 0

    def _on_execute(*args, **kwargs):
        nonlocal count
        count += 1

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _on_execute)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", _on_execute)
    return result, count


def test_list_students_query_count_does_not_scale_with_page_size(db_session):
    centre = _make_school(db_session)
    teacher = Teacher(name="T", phone="919000000001", centre_id=centre.id, role="teacher")
    db_session.add(teacher)
    db_session.commit()

    def make_students(n, offset):
        for i in range(n):
            s = Student(name=f"Student {offset+i}", phone=f"91900000{offset+i:04d}", centre_id=centre.id)
            db_session.add(s)
        db_session.commit()

    make_students(3, 0)
    _, count_small = _count_queries(db_session, lambda: list_students(limit=100, offset=0, db=db_session, teacher=teacher))

    make_students(20, 100)
    _, count_large = _count_queries(db_session, lambda: list_students(limit=100, offset=0, db=db_session, teacher=teacher))

    # The real bug: query count grew linearly with student count (3 extra
    # queries per student). After the fix it's flat — same handful of
    # queries (roster fetch + 3 batched lookups) regardless of page size.
    assert count_large <= count_small + 2, (
        f"query count grew with student count ({count_small} -> {count_large}) — looks like the N+1 regressed"
    )


def test_list_students_returns_correct_balance_referral_and_parent(db_session):
    centre = _make_school(db_session)
    teacher = Teacher(name="T", phone="919000000002", centre_id=centre.id, role="teacher")
    db_session.add(teacher)

    student = Student(name="Nikhil", phone="919000000099", centre_id=centre.id)
    db_session.add(student)
    db_session.commit()

    cost_tracker.add_credits(db_session, student.id, 100.0, note="top-up")
    cost_tracker.grant_referral_credit(db_session, student.id, 10.0, note="referral")
    db_session.add(Parent(student_id=student.id, name="Parent Name", phone="919000000098"))
    db_session.commit()

    result = list_students(limit=100, offset=0, db=db_session, teacher=teacher)

    assert len(result) == 1
    row = result[0]
    assert row["credit_balance"] == 110.0
    assert row["referral_credits_earned"] == 10.0
    assert row["parent_name"] == "Parent Name"
    assert row["parent_phone"] == "919000000098"


def test_list_students_still_scoped_to_own_centre(db_session):
    school_a = _make_school(db_session, "School A")
    school_b = _make_school(db_session, "School B")
    teacher_a = Teacher(name="Teacher A", phone="919000000003", centre_id=school_a.id, role="teacher")
    db_session.add(teacher_a)
    db_session.add(Student(name="A Student", phone="919000000097", centre_id=school_a.id))
    db_session.add(Student(name="B Student", phone="919000000096", centre_id=school_b.id))
    db_session.commit()

    result = list_students(limit=100, offset=0, db=db_session, teacher=teacher_a)

    assert len(result) == 1
    assert result[0]["name"] == "A Student"
