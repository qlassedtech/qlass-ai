-- Backfill of columns/tables that exist in production and in the
-- SQLAlchemy models (backend/app/models/core.py) but were never given a
-- migration file — added live by hand, so a fresh database built from
-- schema.sql + migrations/ could not run the current code (ops audit,
-- Sept 2026; see scripts/check_schema_drift.py, which now runs on every
-- deploy to catch the next one). Every statement is idempotent: this is a
-- complete no-op on production, which already has all of it, and a real
-- backfill on any database rebuilt from the repo.
--
-- Types/defaults mirror the models exactly:
--   Student.email              Text, unique=True           (Google-linked login, null for most)
--   Student.tutor_level        Integer, default=4          (model tier; every student starts at 4)
--   Student.pending_level_offer Integer, nullable          (level being offered while a downgrade nudge awaits a reply)
--   Student.level_nudges_sent  JSONType, default=list      (which threshold nudges went out — "50"/"75")
--   Teacher.email              Text, unique=True           (Google sign-in accounts)
--   Teacher.token_version      Integer, default=0          (bumped on password reset to revoke JWTs)
--   AuditLog                   audit_logs table            (actor attribution for admin actions)

-- students -----------------------------------------------------------------
ALTER TABLE students ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS tutor_level INTEGER NOT NULL DEFAULT 4;
ALTER TABLE students ADD COLUMN IF NOT EXISTS pending_level_offer INTEGER;
ALTER TABLE students ADD COLUMN IF NOT EXISTS level_nudges_sent JSONB NOT NULL DEFAULT '[]';

-- Unique on email. Production may already enforce this via a
-- `students_email_key` UNIQUE constraint (what Base.metadata.create_all
-- emits) rather than an index by our name, so check for ANY unique index
-- on the column instead of relying on IF NOT EXISTS by name — otherwise
-- this would add a second, redundant unique index there.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = current_schema() AND tablename = 'students'
          AND indexdef ILIKE 'CREATE UNIQUE INDEX%(email)'
    ) THEN
        CREATE UNIQUE INDEX idx_students_email ON students (email);
    END IF;
END $$;

-- teachers -----------------------------------------------------------------
ALTER TABLE teachers ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE teachers ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = current_schema() AND tablename = 'teachers'
          AND indexdef ILIKE 'CREATE UNIQUE INDEX%(email)'
    ) THEN
        CREATE UNIQUE INDEX idx_teachers_email ON teachers (email);
    END IF;
END $$;

-- audit_logs ---------------------------------------------------------------
-- Exactly the AuditLog model: the model declares no secondary indexes, so
-- only the primary key + FK are created here.
CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    actor_teacher_id INTEGER REFERENCES teachers(id),
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    detail TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
