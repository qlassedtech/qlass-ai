ALTER TABLE students ADD COLUMN IF NOT EXISTS is_staff_profile BOOLEAN DEFAULT FALSE;

-- Backfill: any existing student row whose phone matches a teacher's own
-- phone (within the same school) was created by the "My AI Tutor"
-- lazy-provisioning path, not a real enrollment.
UPDATE students
SET is_staff_profile = TRUE
WHERE EXISTS (
    SELECT 1 FROM teachers
    WHERE teachers.phone = students.phone
    AND teachers.centre_id IS NOT DISTINCT FROM students.centre_id
);
