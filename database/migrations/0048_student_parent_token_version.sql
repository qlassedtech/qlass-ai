-- Mirrors teachers.token_version (see Student.token_version /
-- app.services.student_auth): stamped into every student/parent JWT and
-- bumped when a student's password is set by an admin, so older tokens
-- stop working immediately instead of living out their full week.
ALTER TABLE students ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE parents ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;
