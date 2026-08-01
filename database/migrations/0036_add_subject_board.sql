-- A (class, name) subject can now legitimately repeat under a different
-- board with a genuinely different syllabus (e.g. BSEB Class 10 Hindi vs
-- CBSE/NCERT Class 10 Hindi) — board is part of the subject's identity.
-- Existing rows predate this column and are all NCERT/CBSE-aligned.
ALTER TABLE subjects ADD COLUMN IF NOT EXISTS board TEXT DEFAULT 'CBSE';
UPDATE subjects SET board = 'CBSE' WHERE board IS NULL;
ALTER TABLE subjects DROP CONSTRAINT IF EXISTS uq_subjects_class_name_board;
ALTER TABLE subjects ADD CONSTRAINT uq_subjects_class_name_board UNIQUE (class, name, board);
