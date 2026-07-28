-- chat_history and topic_progress had no index on student_id at all —
-- every tutoring turn queries both filtered by student_id (recent history,
-- weak-topics lookup, message-count check), which was a full table scan on
-- both. Composite indexes matching the actual query shape (student_id +
-- ordering/filter column) fix this without changing any behavior.
CREATE INDEX IF NOT EXISTS idx_chat_history_student_created ON chat_history(student_id, created_at);
CREATE INDEX IF NOT EXISTS idx_topic_progress_student_correct_created ON topic_progress(student_id, is_correct, created_at);
