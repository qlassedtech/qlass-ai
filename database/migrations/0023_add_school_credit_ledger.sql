CREATE TABLE IF NOT EXISTS school_credit_events (
    id SERIAL PRIMARY KEY,
    amount NUMERIC NOT NULL,
    service TEXT,
    raw_cost NUMERIC,
    centre_id INTEGER NOT NULL REFERENCES centres(id),
    note TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_school_credit_events_centre ON school_credit_events(centre_id);
