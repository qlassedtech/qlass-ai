-- Tracks which state a student is in. Launching in Bihar only for now, so
-- new students default there; this also anchors which regional languages
-- (Hindi/Bhojpuri/Magahi family vs. others) are realistic for detection.
ALTER TABLE students ADD COLUMN IF NOT EXISTS state TEXT DEFAULT 'Bihar';
