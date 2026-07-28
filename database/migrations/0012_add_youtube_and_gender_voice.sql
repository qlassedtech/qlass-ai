-- New "youtube_videos" feature flag (best-matching video suggestions).
-- Existing rows predate this key entirely, so has_feature("youtube_videos")
-- would silently read as false for everyone, including the already-full-
-- access demo numbers — backfill it to match each student's existing
-- access level (full-access demo students get it too, everyone else stays
-- off, same posture as the other premium features).
UPDATE students
SET features = features || '{"youtube_videos": true}'::jsonb
WHERE phone IN ('918789674434', '918460184666', '918252345266');

UPDATE students
SET features = features || '{"youtube_videos": false}'::jsonb
WHERE NOT (features ? 'youtube_videos');

ALTER TABLE students ALTER COLUMN features SET DEFAULT
    '{"voice": false, "ocr": false, "image_generation": false, "documents": false, "youtube_videos": false}';
