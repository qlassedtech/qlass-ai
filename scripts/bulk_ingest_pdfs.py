"""
Ingests a whole directory of textbook chapter files (PDF/.docx/.txt) at
once, driven by a manifest CSV — the actual tool for loading a downloaded
NCERT/SCERT/CBSE-book library, as opposed to scripts/ingest_document.py's
one-chapter-at-a-time usage.

The manifest has one row per file, with these columns (header required,
any order):
    filename,class,subject,chapter,board

`filename` is relative to the directory passed on the command line. Rows
with a missing file are reported and skipped (not fatal — a partial
manifest shouldn't block ingesting everything else). Reuses
scripts/ingest_document.py's own ingest() function so both tools stay in
sync on chunking/embedding behavior — there is exactly one place that
logic lives.

Usage:
    python scripts/bulk_ingest_pdfs.py /path/to/ncert_pdfs manifest.csv
"""
import argparse
import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from ingest_document import ingest  # noqa: E402

REQUIRED_COLUMNS = {"filename", "class", "subject", "chapter", "board"}


async def bulk_ingest(directory: str, manifest_path: str) -> None:
    base = Path(directory)
    with open(manifest_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing_columns = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing_columns:
            print(f"Manifest is missing required column(s): {', '.join(sorted(missing_columns))}")
            return
        rows = list(reader)

    if not rows:
        print("Manifest has no rows — nothing to ingest.")
        return

    succeeded, failed, missing = 0, 0, 0
    for row in rows:
        file_path = base / row["filename"]
        if not file_path.exists():
            print(f"SKIP (file not found): {file_path}")
            missing += 1
            continue
        try:
            await ingest(str(file_path), row["class"], row["subject"], row["chapter"], row["board"])
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 — one bad chapter shouldn't abort the whole batch
            print(f"FAILED ({file_path}): {exc}")
            failed += 1

    print(f"\nDone: {succeeded} ingested, {failed} failed, {missing} missing files, {len(rows)} total rows.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", help="Directory containing the chapter files referenced by the manifest")
    parser.add_argument("manifest", help="Path to the manifest CSV (filename,class,subject,chapter,board)")
    args = parser.parse_args()
    asyncio.run(bulk_ingest(args.directory, args.manifest))


if __name__ == "__main__":
    main()
