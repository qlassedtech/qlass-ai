-- Per-student feature flags (voice, OCR, image generation, ...). Lets each
-- account be configured at onboarding for which premium capabilities are
-- available, rather than a single global on/off switch. Defaults to all
-- enabled for now (demo phase); real customer onboarding will set this
-- explicitly per account.
ALTER TABLE students ADD COLUMN IF NOT EXISTS features JSONB
    DEFAULT '{"voice": true, "ocr": true, "image_generation": true}';
