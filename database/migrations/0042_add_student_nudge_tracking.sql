-- Re-engagement nudge tracking (see app.services.nudges and
-- scripts/send_engagement_nudges.py). nudges_sent records the last-sent
-- timestamp per nudge type so the rotation never repeats a type inside its
-- cooldown window; nudges_opt_out is set when a student texts "stop
-- nudges"/"unsubscribe" (see app.routers.whatsapp).
ALTER TABLE students ADD COLUMN IF NOT EXISTS nudges_sent JSONB DEFAULT '{}'::jsonb;
ALTER TABLE students ADD COLUMN IF NOT EXISTS nudges_opt_out BOOLEAN DEFAULT false;
