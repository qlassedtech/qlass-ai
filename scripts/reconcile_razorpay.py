"""
Read-only reconciliation between Razorpay's own payment records and our
internal ledgers (credit_events for student wallet top-ups, school_credit_
events for school-level top-ups) — both ledgers already store the Razorpay
payment_id in their external_ref column (see /pay/verify and
/admin/school/pay/verify), so a captured Razorpay payment with no matching
external_ref means a payment succeeded but was never credited internally
(e.g. the browser tab closed before /pay/verify ran) — the exact kind of
gap manual finance review needs to catch and fix by hand.

This script only reads and prints a report — it makes no writes to
Razorpay or to our own database, so it's always safe to run.

Usage:
    python scripts/reconcile_razorpay.py [--days 30]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import CreditEvent, SchoolCreditEvent  # noqa: E402
from app.services.razorpay_client import client  # noqa: E402


def _captured_payment_ids(days: int) -> list[dict]:
    since = int(time.time()) - days * 86400
    payments = []
    skip = 0
    count = 100
    while True:
        page = client.payment.all({"from": since, "count": count, "skip": skip})["items"]
        payments.extend(page)
        if len(page) < count:
            break
        skip += count
    return [p for p in payments if p.get("status") == "captured"]


def reconcile(days: int) -> None:
    if client is None:
        print("Razorpay isn't configured (missing key/secret in .env) — nothing to reconcile.")
        return

    db = SessionLocal()
    try:
        captured = _captured_payment_ids(days)
        known_refs = {
            r[0] for r in db.query(CreditEvent.external_ref).filter(CreditEvent.external_ref.isnot(None)).all()
        } | {
            r[0] for r in db.query(SchoolCreditEvent.external_ref).filter(SchoolCreditEvent.external_ref.isnot(None)).all()
        }

        missing = [p for p in captured if p["id"] not in known_refs]

        print(f"Checked {len(captured)} captured Razorpay payment(s) from the last {days} day(s).")
        if not missing:
            print("All captured payments are accounted for in our ledgers. Nothing to reconcile.")
            return

        print(f"\n{len(missing)} captured payment(s) have NO matching credit in our ledger:\n")
        for p in missing:
            amount_inr = p["amount"] / 100  # Razorpay amounts are in paise
            print(f"  payment_id={p['id']}  amount=₹{amount_inr:.2f}  order_id={p.get('order_id')}  created_at={p['created_at']}")
        print("\nEach of these needs manual review — likely a payment that succeeded on Razorpay's side but where "
              "/pay/verify (or /admin/school/pay/verify) never ran to credit the wallet.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="How many days back to check (default: 30)")
    args = parser.parse_args()
    reconcile(args.days)


if __name__ == "__main__":
    main()
