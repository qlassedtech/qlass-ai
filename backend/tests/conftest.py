"""
In-memory SQLite test DB — completely isolated from the real dev/prod
Postgres database, so tests never touch or risk real data. Student.features
etc. use a JSONB/JSON variant column specifically so this works (see
app.models.core.JSONType).
"""
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import redis as redis_sync
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# --- Redis isolation -------------------------------------------------------
# Several helpers (rate_limit, otp, alerts, active_profile) keep real state
# in Redis and the tests exercise them for real when a server is reachable.
# scripts/deploy.sh runs this suite ON the production box, where REDIS_URL
# (env or .env) is the live instance — so before `app.config.settings` is
# ever built, REDIS_URL is rewritten to the same server's database 15,
# which nothing else uses, and that database is flushed at session start
# and end. Env vars beat the .env file in pydantic-settings, so every
# module-level client built from settings.redis_url lands on /15.
TEST_REDIS_DB = 15
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _dotenv_value(key: str) -> str | None:
    env_file = _REPO_ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def _test_redis_url() -> str:
    base = os.environ.get("REDIS_URL") or _dotenv_value("REDIS_URL") or "redis://localhost:6379"
    parts = urlsplit(base)
    # Only the path (the database index) changes — scheme, user:password@host:port and any query survive.
    return urlunsplit((parts.scheme or "redis", parts.netloc, f"/{TEST_REDIS_DB}", parts.query, ""))


TEST_REDIS_URL = _test_redis_url()
os.environ["REDIS_URL"] = TEST_REDIS_URL

from app.database import Base  # noqa: E402 - must come after the REDIS_URL override above
import app.models.core  # noqa: E402,F401 - registers models on Base


def _flush_test_redis() -> None:
    """FLUSHDB the test database if the server is reachable; silently a no-op otherwise (the fail-open paths cover it)."""
    try:
        client = redis_sync.Redis.from_url(TEST_REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
        # Belt and braces: never flush anything but the dedicated test index.
        if client.connection_pool.connection_kwargs.get("db") != TEST_REDIS_DB:
            return
        client.flushdb()
        client.close()
    except (redis_sync.exceptions.RedisError, OSError):
        pass


@pytest.fixture(scope="session", autouse=True)
def _isolated_test_redis():
    _flush_test_redis()
    yield
    _flush_test_redis()


@pytest.fixture()
def db_session():
    # StaticPool (a single shared connection reused by every caller) rather
    # than SQLAlchemy's plain default for a bare "sqlite:///:memory:" URL
    # (SingletonThreadPool, one connection per thread) — a FastAPI
    # TestClient websocket_connect (see tests/test_voice_call.py) runs the
    # ASGI app in a background worker thread, so a per-thread pool handed
    # that thread a second, completely empty in-memory database the moment
    # any query executed there ("no such table: students"), even though
    # this fixture's setup ran fine in the main test thread just before.
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
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
