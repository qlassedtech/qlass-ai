-- Firebase Cloud Messaging device token for the native Android student app,
-- so push notifications (streak/habit reminders) can reach app users
-- alongside the existing WhatsApp-only nudge scripts.
ALTER TABLE students ADD COLUMN IF NOT EXISTS fcm_token TEXT;
