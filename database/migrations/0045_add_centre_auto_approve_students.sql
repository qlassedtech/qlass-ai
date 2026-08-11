-- Whether a student self-registering through THIS school's own link (see
-- app.routers.public.register) becomes a full roster member immediately,
-- or starts "pending" until a teacher confirms them (see
-- Student.approval_status / GET /admin/students/pending). Defaults to
-- true (auto-approve) — matches the behavior every school already had
-- before the approval workflow existed, so this is opt-in per school via
-- PATCH /admin/school, not a disruptive default change.
ALTER TABLE centres ADD COLUMN IF NOT EXISTS auto_approve_students BOOLEAN DEFAULT true;
