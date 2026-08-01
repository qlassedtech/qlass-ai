-- A school already knows which board it follows — new students under it
-- should default to this instead of being asked individually.
ALTER TABLE centres ADD COLUMN IF NOT EXISTS board TEXT;
