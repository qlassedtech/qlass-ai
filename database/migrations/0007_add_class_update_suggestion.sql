-- Tracks how many recent messages were for a class level different from the
-- student's registered one, so we can suggest updating their profile after
-- a real pattern (not a single off-topic question) — and remembers which
-- class was suggested while awaiting a yes/no answer.
ALTER TABLE students ADD COLUMN IF NOT EXISTS off_level_count INTEGER DEFAULT 0;
ALTER TABLE students ADD COLUMN IF NOT EXISTS suggested_class TEXT;
