-- Pins the most recently uploaded/pasted document (PDF, photo OCR, or a
-- long pasted question set) outside the sliding chat_history window.
-- HISTORY_TURNS only keeps the last ~6 exchanges, so a long problem set
-- (e.g. a 20-question homework PDF) scrolls the original questions out of
-- context after just a few Q&A turns — confirmed live: the tutor lost track
-- of the uploaded DPP sheet by Q5 and asked the student to re-paste it.
-- Storing it here means it stays available for the whole session regardless
-- of how many turns the problem set takes.
ALTER TABLE students ADD COLUMN IF NOT EXISTS active_document_text TEXT;
