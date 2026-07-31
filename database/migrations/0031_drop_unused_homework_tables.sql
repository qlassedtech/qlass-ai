-- homework/homework_submission were scaffolded early on but never wired
-- into any actual assignment/submission flow — confirmed zero rows and
-- zero code references anywhere in the app. Dropping rather than leaving
-- unused, confusing scaffolding around; a real homework feature (photo
-- submission, OCR grading, teacher review) would be a separate, deliberate
-- build with its own schema design, not a resurrection of this one.
DROP TABLE IF EXISTS homework_submission;
DROP TABLE IF EXISTS homework;
