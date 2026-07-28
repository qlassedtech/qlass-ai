-- Academic-integrity signal: how often the tutor gave a hint vs a full
-- worked solution when a student brought a problem to solve, so a teacher
-- can see how much a student leans on direct answers vs working through
-- problems themselves. Counted via the new "solved" field on the TRACK tag.
ALTER TABLE students ADD COLUMN IF NOT EXISTS hints_given_count INTEGER DEFAULT 0;
ALTER TABLE students ADD COLUMN IF NOT EXISTS direct_solutions_count INTEGER DEFAULT 0;
