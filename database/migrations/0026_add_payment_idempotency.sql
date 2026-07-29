ALTER TABLE credit_events ADD COLUMN IF NOT EXISTS external_ref TEXT UNIQUE;
ALTER TABLE school_credit_events ADD COLUMN IF NOT EXISTS external_ref TEXT UNIQUE;
