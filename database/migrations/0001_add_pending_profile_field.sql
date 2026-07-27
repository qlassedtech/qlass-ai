-- Tracks which profile question (class/board/school) the tutor is currently
-- waiting on an answer for, so the next inbound message is treated as that
-- answer instead of a new tutoring question.
ALTER TABLE students ADD COLUMN IF NOT EXISTS pending_profile_field TEXT;
