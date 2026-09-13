-- Per-feature Claude spend visibility (see app.services.cost_tracker.
-- record_claude_usage and app.services.analytics.get_ai_cost_breakdown) —
-- until now, every Claude call was billed with only a model-TIER label
-- (claude_sonnet/claude_haiku, via CreditEvent.service/SchoolCreditEvent.
-- service), so there was no way to tell whether tutoring replies, quizzes,
-- diagrams, notes, workbooks, nudges, or translation actually drove spend.
-- Nullable and backfill-safe, same convention as tutor_style/phone_verified
-- before it — every pre-existing row simply has no feature label, and new
-- code fills it in going forward; no backfill needed since old rows have
-- no way to know retroactively which feature they came from.
ALTER TABLE credit_events ADD COLUMN IF NOT EXISTS feature TEXT;
ALTER TABLE school_credit_events ADD COLUMN IF NOT EXISTS feature TEXT;
