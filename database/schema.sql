-- Qlass AI OS — initial schema (Phase 1/4)
-- Run via: psql $DATABASE_URL -f database/schema.sql
-- This is a starting subset; expand toward the ~40-50 table target as phases progress.

CREATE TABLE IF NOT EXISTS centres (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT UNIQUE NOT NULL,
    class TEXT,
    board TEXT,
    school TEXT,
    preferred_language TEXT DEFAULT 'en',
    centre_id INTEGER REFERENCES centres(id),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS parents (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    name TEXT,
    phone TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS teachers (
    id SERIAL PRIMARY KEY,
    name TEXT,
    phone TEXT UNIQUE,
    centre_id INTEGER REFERENCES centres(id)
);

CREATE TABLE IF NOT EXISTS subjects (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    class TEXT
);

CREATE TABLE IF NOT EXISTS chapters (
    id SERIAL PRIMARY KEY,
    subject_id INTEGER REFERENCES subjects(id),
    name TEXT NOT NULL,
    chapter_no INTEGER
);

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    drive_file_id TEXT UNIQUE,
    title TEXT,
    class TEXT,
    subject TEXT,
    chapter TEXT,
    board TEXT,
    language TEXT,
    difficulty TEXT,
    synced_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id),
    chunk_index INTEGER,
    content TEXT,
    embedding_id TEXT -- pointer into the vector store (Chroma)
);

CREATE TABLE IF NOT EXISTS chat_history (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    role TEXT CHECK (role IN ('user','assistant')),
    message TEXT,
    agent TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quizzes (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    chapter_id INTEGER REFERENCES chapters(id),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS questions (
    id SERIAL PRIMARY KEY,
    quiz_id INTEGER REFERENCES quizzes(id),
    question_type TEXT,
    question_text TEXT,
    correct_answer TEXT
);

CREATE TABLE IF NOT EXISTS answers (
    id SERIAL PRIMARY KEY,
    question_id INTEGER REFERENCES questions(id),
    student_id INTEGER REFERENCES students(id),
    given_answer TEXT,
    is_correct BOOLEAN
);

CREATE TABLE IF NOT EXISTS homework (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    chapter_id INTEGER REFERENCES chapters(id),
    assigned_at TIMESTAMPTZ DEFAULT now(),
    due_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS homework_submission (
    id SERIAL PRIMARY KEY,
    homework_id INTEGER REFERENCES homework(id),
    student_id INTEGER REFERENCES students(id),
    file_url TEXT,
    ocr_text TEXT,
    marks_awarded NUMERIC,
    feedback TEXT,
    submitted_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attendance (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    date DATE,
    status TEXT CHECK (status IN ('present','absent','late'))
);

CREATE TABLE IF NOT EXISTS progress (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    chapter_id INTEGER REFERENCES chapters(id),
    mastery_score NUMERIC,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reports (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    period TEXT,
    summary TEXT,
    generated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    recipient_phone TEXT,
    message TEXT,
    sent_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS study_plans (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    plan_json JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    amount NUMERIC,
    status TEXT,
    paid_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    started_at TIMESTAMPTZ DEFAULT now(),
    ended_at TIMESTAMPTZ
);
