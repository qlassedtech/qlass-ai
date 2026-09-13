-- Classroom/cohort grouping (see app.models.core.Classroom) — lets one
-- teacher group a subset of their school's (centre's) students into a
-- class they can view roster-level progress for, closing the gap against
-- rivals whose whole model is built around this: a teacher creates a
-- classroom, students join it, the teacher sees who needs help. Narrower
-- than Centre (a whole tuition-branch/school) — a centre can have many
-- classrooms, and a student's classroom_id is independent of (but must
-- belong to the same school as) their centre_id.
CREATE TABLE IF NOT EXISTS classrooms (
    id SERIAL PRIMARY KEY,
    centre_id INTEGER NOT NULL REFERENCES centres(id),
    teacher_id INTEGER NOT NULL REFERENCES teachers(id),
    name TEXT NOT NULL,
    board TEXT,
    "class" TEXT,
    subject TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_classrooms_centre ON classrooms(centre_id);

ALTER TABLE students ADD COLUMN IF NOT EXISTS classroom_id INTEGER REFERENCES classrooms(id);
CREATE INDEX IF NOT EXISTS idx_students_classroom ON students(classroom_id);
