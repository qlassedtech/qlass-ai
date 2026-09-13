-- Spaced-repetition revision scheduling (see app.services.revision_scheduler
-- and app.models.core.RevisionSchedule) — a fixed Leitner-style interval
-- ladder driven by TopicProgress results (quiz_flow.py / chat_core.py): a
-- wrong answer schedules a review a few days out, a correct answer on an
-- existing schedule pushes it further out. scripts/send_revision_reminders.py
-- runs daily and queries this table for reviews that are due.
CREATE TABLE IF NOT EXISTS revision_schedule (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    topic TEXT NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    interval_stage INTEGER NOT NULL DEFAULT 0,
    last_reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_revision_schedule_student_due ON revision_schedule (student_id, due_at);
