-- The feature-gating fix in 0011/whatsapp.py only governs NEW students
-- created going forward. It never touched existing rows, so ~18 leftover
-- test-phase students (fake/dummy numbers from earlier development, e.g.
-- 919876543210, 919111111111, etc.) still carried the old permissive
-- all-true defaults — a real leftover cost/abuse surface if any of those
-- numbers were ever reachable. Revoke access for every student NOT on the
-- current full-access allowlist (must match FULL_ACCESS_PHONES in
-- backend/app/routers/whatsapp.py exactly).
UPDATE students
SET features = '{"voice": false, "ocr": false, "image_generation": false, "documents": false, "youtube_videos": false}'::jsonb
WHERE phone NOT IN ('918789674434', '918460184666', '918252345266');
