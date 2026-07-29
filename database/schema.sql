-- Qlass AI OS — initial schema (Phase 1/4)
-- Run via: psql $DATABASE_URL -f database/schema.sql
-- This is a starting subset; expand toward the ~40-50 table target as phases progress.

CREATE TABLE IF NOT EXISTS centres (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT,
    logo_url TEXT,
    sales_status TEXT DEFAULT 'active',
    sales_notes TEXT,
    contract_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    -- Not UNIQUE — a shared family phone can have more than one student
    -- profile (see app.services.active_profile for how the active one is
    -- resolved per message).
    phone TEXT NOT NULL,
    class TEXT,
    board TEXT,
    school TEXT,
    preferred_language TEXT DEFAULT 'en-IN',
    centre_id INTEGER REFERENCES centres(id),
    pending_profile_field TEXT,
    state TEXT DEFAULT 'Bihar',
    features JSONB DEFAULT '{"voice": false, "ocr": false, "image_generation": false, "documents": false, "youtube_videos": false}',
    off_level_count INTEGER DEFAULT 0,
    suggested_class TEXT,
    gender TEXT,
    active_document_text TEXT,
    focus_topic TEXT,
    hints_given_count INTEGER DEFAULT 0,
    direct_solutions_count INTEGER DEFAULT 0,
    photo_url TEXT,
    referral_code TEXT UNIQUE,
    referred_by_id INTEGER REFERENCES students(id),
    referral_milestones_paid JSONB DEFAULT '[]',
    habit_milestones_paid JSONB DEFAULT '[]',
    is_staff_profile BOOLEAN DEFAULT FALSE,
    subscription_plan TEXT DEFAULT 'credits',
    subscription_expires_at TIMESTAMPTZ,
    consent_given_at TIMESTAMPTZ,
    deletion_requested_at TIMESTAMPTZ,
    is_deleted BOOLEAN DEFAULT FALSE,
    consecutive_unresolved_hints INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_students_phone ON students(phone);

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
    centre_id INTEGER REFERENCES centres(id),
    password_hash TEXT,
    role TEXT DEFAULT 'teacher',
    photo_url TEXT
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
CREATE INDEX IF NOT EXISTS idx_chat_history_student_created ON chat_history(student_id, created_at);

CREATE TABLE IF NOT EXISTS quizzes (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    chapter_id INTEGER REFERENCES chapters(id),
    created_by_teacher_id INTEGER REFERENCES teachers(id),
    title TEXT,
    is_mock_test BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);
-- Added here (not in the students CREATE TABLE above) since it references
-- quizzes, which isn't defined until this point in the file.
ALTER TABLE students ADD COLUMN IF NOT EXISTS active_quiz_id INTEGER REFERENCES quizzes(id);

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
CREATE INDEX IF NOT EXISTS idx_topic_progress_student_correct_created ON topic_progress(student_id, is_correct, created_at);

-- Guards against duplicate processing when Wati redelivers/retries a
-- webhook call for a message we already handled.
CREATE TABLE IF NOT EXISTS processed_webhook_messages (
    message_id TEXT PRIMARY KEY,
    processed_at TIMESTAMPTZ DEFAULT now()
);

-- Credit/cost ledger: append-only top-ups (positive amount) and per-request
-- deductions (negative amount, tagged by service). Current balance =
-- SUM(amount). See database/migrations/0009_add_credit_tracking.sql.
CREATE TABLE IF NOT EXISTS credit_events (
    id SERIAL PRIMARY KEY,
    amount NUMERIC NOT NULL,
    service TEXT,
    raw_cost NUMERIC,
    student_id INTEGER REFERENCES students(id),
    note TEXT,
    external_ref TEXT UNIQUE,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Separate ledger from credit_events (per-student) — teacher-facing tools
-- like the workbook PDF generator and Gamma presentations are billed to
-- the SCHOOL, not any one student's wallet.
CREATE TABLE IF NOT EXISTS school_credit_events (
    id SERIAL PRIMARY KEY,
    amount NUMERIC NOT NULL,
    service TEXT,
    raw_cost NUMERIC,
    centre_id INTEGER NOT NULL REFERENCES centres(id),
    note TEXT,
    external_ref TEXT UNIQUE,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_school_credit_events_centre ON school_credit_events(centre_id);
