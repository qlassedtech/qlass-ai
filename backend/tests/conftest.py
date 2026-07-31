"""
In-memory SQLite test DB — completely isolated from the real dev/prod
Postgres database, so tests never touch or risk real data. Student.features
etc. use a JSONB/JSON variant column specifically so this works (see
app.models.core.JSONType).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
import app.models.core  # noqa: F401 - registers models on Base


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# A dedicated Postgres database (NOT the real dev/prod one) for tests that
# depend on genuine tz-aware TIMESTAMPTZ comparisons — SQLite silently
# drops tzinfo on round-trip, which breaks the aware-vs-naive datetime
# comparisons inside evaluate_referral_milestones/evaluate_habit_milestones
# even though that logic is correct against real Postgres (verified live
# during development). Wrapped in a transaction that's always rolled back,
# so tests never leave data behind and can run repeatedly.
PG_TEST_DATABASE_URL = "postgresql://qlass:qlass@localhost:5433/qlass_ai_test"


@pytest.fixture()
def pg_db_session():
    engine = create_engine(PG_TEST_DATABASE_URL)
    # This database is dedicated to tests only. Rebuild its schema so a
    # model/migration change cannot silently run tests against stale tables.
    # DROP SCHEMA avoids the intentional students<->quizzes FK cycle.
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(bind=engine)
    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
