-- preferred_language was defaulting to "en" while every other part of the
-- app (Sarvam language codes, the language classifier) works in "en-IN" /
-- "hi-IN" format. A brand-new student's very first (ambiguous) message
-- could resolve to "en" and get passed straight into Sarvam's TTS
-- target_language_code, which expects the full "en-IN" form. Fix the
-- default going forward and backfill existing rows still on the old value.
ALTER TABLE students ALTER COLUMN preferred_language SET DEFAULT 'en-IN';
UPDATE students SET preferred_language = 'en-IN' WHERE preferred_language = 'en';
