-- Interim topic-wise mastery tracking, ahead of real curriculum data (RAG).
-- Once chapters/subjects are populated from real syllabus content, this can
-- be linked to chapter_id instead of relying on free-text topic names.
CREATE TABLE IF NOT EXISTS topic_progress (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    topic TEXT NOT NULL,
    question_text TEXT,
    given_answer TEXT,
    is_correct BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT now()
);
