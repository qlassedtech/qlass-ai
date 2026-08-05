-- Multi-tenancy/scale audit (10 orgs, 50 schools, 1000 teachers, 10,000
-- students): every admin/teacher dashboard query, and every per-turn
-- credit check, filters by centre_id or student_id — none of those
-- columns had an index, meaning each becomes a full table scan once
-- credit_events/students/teachers grow to realistic size. chat_history and
-- topic_progress already had indexes (see schema.sql); these three were
-- the actual gaps, confirmed via \d against a live database.

CREATE INDEX IF NOT EXISTS idx_credit_events_student_created ON credit_events(student_id, created_at);
CREATE INDEX IF NOT EXISTS idx_students_centre_id ON students(centre_id);
CREATE INDEX IF NOT EXISTS idx_teachers_centre_id ON teachers(centre_id);
