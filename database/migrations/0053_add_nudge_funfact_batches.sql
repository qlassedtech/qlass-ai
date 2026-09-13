-- Reconciliation state for "fun_fact" re-engagement nudges submitted to
-- Anthropic's Message Batches API (see app.services.nudges +
-- scripts/submit_nudge_funfact_batch.py / fetch_nudge_funfact_batch.py).
-- Batches are submit-then-fetch across two separate cron runs — this table
-- is what lets the fetch run map results back to students and avoid
-- re-processing an already-handled batch, the same idea as
-- processed_webhook_messages but for an outbound async job.
CREATE TABLE IF NOT EXISTS nudge_funfact_batches (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'submitted',
    requests JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_nudge_funfact_batches_status ON nudge_funfact_batches (status);
