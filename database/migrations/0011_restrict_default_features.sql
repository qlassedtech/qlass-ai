-- Defense in depth: the app now explicitly sets features on student
-- creation (full access only for the allowlisted demo numbers, nothing for
-- everyone else — see _get_or_create_student in whatsapp.py). Change the
-- column default itself to match ("safe by default") in case any other
-- insert path ever bypasses that helper. Does not touch existing rows —
-- already-provisioned students keep whatever features they were explicitly
-- given.
ALTER TABLE students ALTER COLUMN features SET DEFAULT
    '{"voice": false, "ocr": false, "image_generation": false, "documents": false}';
