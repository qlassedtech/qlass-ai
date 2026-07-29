-- Teacher/admin login for the new web portal. role distinguishes admins
-- (see all students) from teachers (scoped to their centre, once centre
-- assignment is actually populated — currently most students have no
-- centre_id set, so this is forward-looking).
ALTER TABLE teachers ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE teachers ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'teacher';
