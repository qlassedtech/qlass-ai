-- Keep the incoming payload and processing state so a worker crash after
-- acknowledging Wati does not silently lose a student message. A lease lets
-- another worker safely retry a job abandoned by a dead process.
ALTER TABLE processed_webhook_messages ADD COLUMN IF NOT EXISTS payload JSONB;
ALTER TABLE processed_webhook_messages ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE processed_webhook_messages ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE processed_webhook_messages ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE processed_webhook_messages ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_processed_webhook_messages_pending
    ON processed_webhook_messages(status, lease_expires_at);
