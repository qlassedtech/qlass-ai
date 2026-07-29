ALTER TABLE students DROP COLUMN IF EXISTS referral_bonus_paid;
ALTER TABLE students ADD COLUMN IF NOT EXISTS referral_milestones_paid JSONB DEFAULT '[]';
