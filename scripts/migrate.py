"""
Tiny SQL migration runner for database/migrations/*.sql.

Keeps a schema_migrations(filename, applied_at) table and applies every
file not yet recorded, in filename order, each inside its own transaction.

    python scripts/migrate.py             # apply pending migrations
    python scripts/migrate.py --baseline  # record every existing file as applied WITHOUT running it
    python scripts/migrate.py --dry-run   # list what would run

--baseline is for a database that already has every migration applied by
hand (prod at the time this runner was introduced).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import REPO_ROOT, settings  # noqa: E402

MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))
    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(text(CREATE_TABLE_SQL))
        applied = {row[0] for row in conn.execute(text("SELECT filename FROM schema_migrations"))}

    pending = [p for p in files if p.name not in applied]
    if not pending:
        print("no pending migrations")
        return 0

    for path in pending:
        if args.dry_run:
            print(f"pending: {path.name}")
            continue
        with engine.begin() as conn:
            if args.baseline:
                print(f"baseline: {path.name}")
            else:
                print(f"applying: {path.name}")
                # exec_driver_sql so ':' inside SQL (casts, JSON) isn't parsed as a bind param.
                conn.exec_driver_sql(path.read_text())
            conn.execute(text("INSERT INTO schema_migrations (filename) VALUES (:f)"), {"f": path.name})
    return 0


if __name__ == "__main__":
    sys.exit(main())
