"""
Schema drift check: compares the live database (settings.database_url, i.e.
the repo-root .env) against the SQLAlchemy models (Base.metadata) and
exits non-zero when they disagree.

    python scripts/check_schema_drift.py            # table, exit 1 on drift
    python scripts/check_schema_drift.py --quiet    # only print on drift

What counts as drift (exit 1):
  - a model table missing from the database
  - a model column missing from its table            (code will crash on that query)
  - a column in the database the model doesn't know  (the "added by hand,
    never migrated" case the Sept 2026 audit found — a rebuild from the
    repo silently loses it)
What is reported but NOT drift:
  - tables in the database with no model (schema_migrations, legacy tables)
  - columns managed by raw DDL rather than a mapped Column (see
    DDL_MANAGED_COLUMNS — document_chunks.content_tsv / embedding are
    attached via DDL events in app.models.core because their types are
    Postgres-only)
Column TYPES are deliberately not compared: JSONB-vs-JSON variants, TEXT vs
VARCHAR and Integer vs SERIAL all read differently through reflection
without being real drift; presence/absence is what breaks a rebuild.

deploy.sh runs this right after migrate.py as a WARNING (non-fatal), so a
drift shows up in every deploy log until someone writes the migration.
Also useful against a scratch database built from schema.sql + migrations
to prove the repo can actually recreate production (CI does this).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import MetaData, create_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
import app.models.core  # noqa: E402,F401 - registers every model on Base

# (table, column) pairs that exist in the DB by design without a mapped Column.
DDL_MANAGED_COLUMNS = {
    ("document_chunks", "content_tsv"),
    ("document_chunks", "embedding"),
}


def compare(model_metadata, live_metadata) -> tuple[list[dict], list[str]]:
    """Returns (rows, untracked_tables). Each row: table, status, missing, extra."""
    rows = []
    live_tables = live_metadata.tables
    for name in sorted(model_metadata.tables):
        model_cols = {c.name for c in model_metadata.tables[name].columns}
        if name not in live_tables:
            rows.append({"table": name, "status": "MISSING TABLE", "missing": sorted(model_cols), "extra": []})
            continue
        live_cols = {c.name for c in live_tables[name].columns}
        missing = sorted(model_cols - live_cols)
        extra = sorted(c for c in live_cols - model_cols if (name, c) not in DDL_MANAGED_COLUMNS)
        status = "ok" if not missing and not extra else "DRIFT"
        rows.append({"table": name, "status": status, "missing": missing, "extra": extra})
    untracked = sorted(set(live_tables) - set(model_metadata.tables))
    return rows, untracked


def print_table(rows: list[dict], untracked: list[str], quiet: bool) -> None:
    drift_rows = [r for r in rows if r["status"] != "ok"]
    if quiet and not drift_rows:
        return
    width = max(len(r["table"]) for r in rows) if rows else 10
    print(f"{'table'.ljust(width)}  {'status'.ljust(13)}  missing in DB (model has)         extra in DB (model lacks)")
    print("-" * (width + 80))
    for r in rows if not quiet else drift_rows:
        print(f"{r['table'].ljust(width)}  {r['status'].ljust(13)}  "
              f"{', '.join(r['missing']) or '-':<34} {', '.join(r['extra']) or '-'}")
    if untracked:
        print(f"\n(no model, ignored: {', '.join(untracked)})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quiet", action="store_true", help="print nothing unless drift is found")
    parser.add_argument("--database-url", default=None, help="override settings.database_url (e.g. a scratch DB)")
    args = parser.parse_args()

    engine = create_engine(args.database_url or settings.database_url)
    live = MetaData()
    live.reflect(bind=engine)
    engine.dispose()

    rows, untracked = compare(Base.metadata, live)
    print_table(rows, untracked, args.quiet)

    drift = [r for r in rows if r["status"] != "ok"]
    if drift:
        print(f"\nSCHEMA DRIFT: {len(drift)} table(s) differ from the models — add a migration under database/migrations/")
        return 1
    if not args.quiet:
        print(f"\nno schema drift ({len(rows)} model tables checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
