-- A prospective-student phone number an external lead-nurture portal is
-- driving directly (see app.routers.leads) — outreach, nudges, replies,
-- entirely outside the AI tutor. Registering a number here tells
-- app.routers.whatsapp NOT to auto-enroll it as a new tutor student the
-- way any other cold-start message would; inbound messages from a
-- registered lead are forwarded to LEADS_WEBHOOK_URL instead.
CREATE TABLE IF NOT EXISTS leads (
    id SERIAL PRIMARY KEY,
    phone TEXT NOT NULL UNIQUE,
    name TEXT,
    -- The portal's own identifier for this lead, if it has one — round-
    -- tripped back on every inbound-message webhook delivery.
    external_ref TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads (phone);
