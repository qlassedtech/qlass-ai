ALTER TABLE students ADD COLUMN IF NOT EXISTS habit_milestones_paid JSONB DEFAULT '[]';
