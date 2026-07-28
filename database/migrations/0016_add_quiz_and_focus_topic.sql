-- active_quiz_id tracks whether a student is mid-quiz (structured quiz
-- mode, using the existing quizzes/questions/answers tables which were
-- defined in the schema from day one but never wired up to any code).
-- focus_topic lets a teacher steer what the tutor prioritizes for a
-- specific student (e.g. "focus on quadratic equations this week").
ALTER TABLE students ADD COLUMN IF NOT EXISTS active_quiz_id INTEGER REFERENCES quizzes(id);
ALTER TABLE students ADD COLUMN IF NOT EXISTS focus_topic TEXT;
