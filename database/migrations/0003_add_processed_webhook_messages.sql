-- Guards against duplicate processing when Wati redelivers/retries a
-- webhook call for a message we already handled (seen in production: a
-- server restart mid-request can cause a reply to be sent successfully but
-- the 200 response never reach Wati, triggering a retry that reprocesses
-- the same message and sends a second, confusing reply).
CREATE TABLE IF NOT EXISTS processed_webhook_messages (
    message_id TEXT PRIMARY KEY,
    processed_at TIMESTAMPTZ DEFAULT now()
);
