ALTER TABLE students ADD COLUMN IF NOT EXISTS referral_code TEXT UNIQUE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS referred_by_id INTEGER REFERENCES students(id);
ALTER TABLE students ADD COLUMN IF NOT EXISTS referral_bonus_paid BOOLEAN DEFAULT FALSE;

INSERT INTO centres (name, city)
SELECT 'Qlass Direct', NULL
WHERE NOT EXISTS (SELECT 1 FROM centres WHERE name = 'Qlass Direct');
