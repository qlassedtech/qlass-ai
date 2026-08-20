-- False only for the Google-registration path (see
-- app.routers.admin.register_school_verify) — Google's email_verified
-- proves the email, never the phone typed alongside it. Checked by
-- /auth/request-teacher-otp before allowing WhatsApp-OTP login: without
-- this, whoever actually controls that unverified number could OTP their
-- way into an account that isn't theirs. DEFAULT true backfills every
-- pre-existing account as verified (admin-added teachers, bulk roster
-- upload, and every account that existed before this column did).
ALTER TABLE teachers ADD COLUMN IF NOT EXISTS phone_verified BOOLEAN DEFAULT true;
