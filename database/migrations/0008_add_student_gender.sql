-- Tracks student gender so voice replies can use the opposite-gender
-- speaker (a female voice for a male student and vice versa).
ALTER TABLE students ADD COLUMN IF NOT EXISTS gender TEXT;
