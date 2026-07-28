-- Allows more than one Student row to share a phone number — needed for
-- shared family WhatsApp numbers (common in Bihar), where two siblings
-- using the same phone previously got merged into a single blended
-- identity (one class/board/progress history for both kids). Which
-- profile is "active" for a given incoming message is now resolved at the
-- application layer (see app.services.active_profile), not by the DB.
ALTER TABLE students DROP CONSTRAINT IF EXISTS students_phone_key;
CREATE INDEX IF NOT EXISTS idx_students_phone ON students(phone);
