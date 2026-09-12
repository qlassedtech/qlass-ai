-- Selectable tutor teaching style (see app.agents.tutor_agent.build_context
-- and app.models.core.Student.tutor_style) — "balanced" is today's existing
-- soft-hint behavior, unchanged default for every existing student;
-- "hint_first" is a new, stricter Socratic mode a student opts into via
-- WhatsApp ("hint mode on") or the student portal toggle.
ALTER TABLE students ADD COLUMN IF NOT EXISTS tutor_style TEXT NOT NULL DEFAULT 'balanced';
