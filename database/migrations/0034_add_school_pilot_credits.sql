ALTER TABLE centres ADD COLUMN IF NOT EXISTS pilot_status TEXT DEFAULT 'none';
ALTER TABLE centres ADD COLUMN IF NOT EXISTS pilot_started_at TIMESTAMPTZ;
ALTER TABLE centres ADD COLUMN IF NOT EXISTS pilot_expires_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS school_pilot_grants (
    id SERIAL PRIMARY KEY,
    centre_id INTEGER NOT NULL REFERENCES centres(id),
    student_id INTEGER NOT NULL REFERENCES students(id),
    pilot_started_at TIMESTAMPTZ NOT NULL,
    amount NUMERIC NOT NULL CHECK (amount > 0),
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (centre_id, student_id, pilot_started_at)
);
