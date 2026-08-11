-- Two related gaps in Student identity, both keyed off the same underlying
-- limitation (phone is currently the sole, immutable login/routing key):
--
-- password_hash: lets a school set a portal password for a student who
-- doesn't have WhatsApp at all — until now, Student had no password field
-- (only Teacher did), and the ONLY login path was a WhatsApp-delivered
-- OTP, so a WhatsApp-less student genuinely could not log in.
--
-- whatsapp_phone: lets a student's real WhatsApp number differ from their
-- primary/login `phone` — e.g. their registered contact number has no
-- WhatsApp, but a parent's or a different personal number does. See
-- app.routers.whatsapp._resolve_active_student, which now matches an
-- inbound message against EITHER column.
ALTER TABLE students ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS whatsapp_phone TEXT;
CREATE INDEX IF NOT EXISTS idx_students_whatsapp_phone ON students (whatsapp_phone) WHERE whatsapp_phone IS NOT NULL;
