-- A group of schools/centres under one umbrella account (e.g. a state
-- government programme spanning many schools) — an org_admin teacher
-- manages every centre under one organization_id, the same way a
-- super_admin manages every centre on the whole platform.
CREATE TABLE IF NOT EXISTS organizations (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    org_type TEXT DEFAULT 'school_group',
    created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE centres ADD COLUMN IF NOT EXISTS organization_id INTEGER REFERENCES organizations(id);
CREATE INDEX IF NOT EXISTS idx_centres_organization ON centres(organization_id);

ALTER TABLE teachers ADD COLUMN IF NOT EXISTS organization_id INTEGER REFERENCES organizations(id);
CREATE INDEX IF NOT EXISTS idx_teachers_organization ON teachers(organization_id);
