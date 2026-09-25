-- Ties every Gamma presentation generation to the school (centre) and
-- teacher that started it (see app.models.core.PresentationJob and
-- app.routers.admin generate_presentation/presentation_status). Before
-- this, nothing recorded which centre a generation_id belonged to: any
-- signed-in teacher could poll any id, and the POLLER's centre was billed
-- (a NOT NULL crash for org_admin, who has no centre_id). `billed` guards
-- against double-billing across the frontend's repeated status polls.
CREATE TABLE IF NOT EXISTS presentation_jobs (
    generation_id TEXT PRIMARY KEY,
    centre_id INTEGER NOT NULL REFERENCES centres(id),
    teacher_id INTEGER NOT NULL REFERENCES teachers(id),
    status TEXT NOT NULL DEFAULT 'pending',
    billed BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_presentation_jobs_centre ON presentation_jobs (centre_id, created_at);
