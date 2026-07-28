-- Credit/cost ledger: a single append-only table of credit top-ups
-- (positive amount) and per-request deductions (negative amount, tagged
-- with which service incurred it). Current balance = SUM(amount). Keeping
-- it append-only (rather than a mutable balance column) gives a full audit
-- trail of exactly what was charged for, per student, over time.
CREATE TABLE IF NOT EXISTS credit_events (
    id SERIAL PRIMARY KEY,
    amount NUMERIC NOT NULL,           -- positive = top-up, negative = deduction (in INR)
    service TEXT,                      -- e.g. 'claude_sonnet', 'sarvam_tts' — null for top-ups
    raw_cost NUMERIC,                  -- actual provider cost before the markup multiplier
    student_id INTEGER REFERENCES students(id),
    note TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_credit_events_created_at ON credit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_credit_events_student_id ON credit_events(student_id);
