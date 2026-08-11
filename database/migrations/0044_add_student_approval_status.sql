-- A student who self-registers through a SPECIFIC school's link (see
-- app.routers.public.register, /join?school=<slug>) now starts as
-- "pending" instead of immediately "approved" — a school wants a teacher
-- to confirm this is actually their own student before treating them as a
-- real enrolled member of the roster, not just anyone who found the link.
-- Not a hard gate on chatting (they still get the welcome message and
-- trial credits right away, same as before) — purely a review/visibility
-- flag surfaced separately from the main roster (see
-- GET /admin/students/pending, POST /admin/students/{id}/approve).
-- Every EXISTING student, and every admin/teacher-provisioned one (see
-- POST /admin/students), defaults to "approved" — this only applies to
-- the self-registration path going forward.
ALTER TABLE students ADD COLUMN IF NOT EXISTS approval_status TEXT DEFAULT 'approved';
UPDATE students SET approval_status = 'approved' WHERE approval_status IS NULL;
