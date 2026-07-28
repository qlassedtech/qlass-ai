-- Adds a "documents" (PDF/Word upload) feature flag alongside voice/ocr/
-- image_generation. Existing rows already have a features JSONB value from
-- migration 0005, so the new key needs an explicit backfill (unlike a fresh
-- column default, which only applies to rows that don't have the column
-- populated yet).
UPDATE students SET features = features || '{"documents": true}'::jsonb
WHERE features IS NOT NULL AND NOT (features ? 'documents');

ALTER TABLE students ALTER COLUMN features
    SET DEFAULT '{"voice": true, "ocr": true, "image_generation": true, "documents": true}';
