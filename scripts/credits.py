"""
View or top up a specific student's AI credit wallet, or estimate typical
per-turn cost. Credits are per-student now (each student has their own
wallet) — use the admin portal for routine top-ups; this is a CLI
convenience for the same operations.

Usage:
    python scripts/credits.py balance <phone>
    python scripts/credits.py add <phone> 50 "manual top-up"
    python scripts/credits.py estimate
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Student  # noqa: E402
from app.services import cost_tracker  # noqa: E402


def _get_student(db, phone: str) -> Student | None:
    return db.query(Student).filter(Student.phone == phone).first()


def _estimate() -> None:
    """
    Rough per-turn cost for a few usage profiles, using real measured Claude
    token counts plus the documented per-unit rates for translation/voice/
    image add-ons — see cost_tracker.PRICING for the per-unit numbers this
    is built from (Sarvam rates are exact, from a real invoice; Claude/
    Azure rates are estimates until calibrated against a real bill).

    Two cost profiles shown per chat type: the FIRST turn of a session
    (system prompt not cached yet — pays the cache-write premium) and every
    SUBSEQUENT turn within the same ~5-minute window (system prompt hits
    Anthropic's prompt cache at ~10% of normal input cost). Measured live:
    first-turn main reply ~₹0.77 raw, cached-turn main reply ~₹0.24 raw.
    """
    m = cost_tracker.MARKUP_MULTIPLIER
    p = cost_tracker.PRICING

    first_turn_raw = 0.766715 + 0.02226
    cached_turn_raw = 0.242026 + 0.02247
    first_turn = first_turn_raw * m
    cached_turn = cached_turn_raw * m

    haiku = p["claude_haiku"]
    translate_raw = (150 / 1000) * haiku["input_per_1k_tokens"] + (120 / 1000) * haiku["output_per_1k_tokens"]
    hindi_add_on = translate_raw * m

    stt_raw = (12 / 60) * p["sarvam_stt"]["per_minute"]
    tts_raw = 400 * p["sarvam_tts"]["per_char"]
    voice_add_on = (stt_raw + tts_raw) * m

    image_add_on = p["azure_image"]["per_call"] * m

    print(f"Trial credit granted to every new student: ₹{cost_tracker.TRIAL_CREDITS:.2f}\n")
    print(f"{'Chat type':<32}{'1st turn':>12}{'cached turn':>14}")
    print(f"{'Plain English text':<32}₹{first_turn:>10.2f}₹{cached_turn:>12.2f}")
    print(f"{'Hindi text (w/ translation)':<32}₹{first_turn + hindi_add_on:>10.2f}₹{cached_turn + hindi_add_on:>12.2f}")
    print(
        f"{'Voice in + voice reply':<32}₹{first_turn + hindi_add_on + voice_add_on:>10.2f}"
        f"₹{cached_turn + hindi_add_on + voice_add_on:>12.2f}"
    )
    print(f"\n(+ ₹{image_add_on:.2f} extra whenever a diagram is generated)")
    print("Cache hits require turns within ~5 min of each other — a slow/cold session pays the '1st turn' rate again.")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return

    if sys.argv[1] == "estimate":
        _estimate()
        return

    if len(sys.argv) < 3:
        print(__doc__)
        return

    db = SessionLocal()
    try:
        phone = sys.argv[2]
        student = _get_student(db, phone)
        if not student:
            print(f"No student found with phone {phone}")
            return

        if sys.argv[1] == "balance":
            print(f"{student.name} ({phone}): ₹{cost_tracker.get_balance(db, student.id):.2f}")
        elif sys.argv[1] == "add":
            amount = float(sys.argv[3])
            note = sys.argv[4] if len(sys.argv) > 4 else None
            new_balance = cost_tracker.add_credits(db, student.id, amount, note)
            print(f"Added ₹{amount:.2f} to {student.name} ({phone}). New balance: ₹{new_balance:.2f}")
        else:
            print(__doc__)
    finally:
        db.close()


if __name__ == "__main__":
    main()
