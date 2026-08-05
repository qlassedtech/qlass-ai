def normalize_phone(raw: str) -> str:
    """
    Stored consistently with every phone in this codebase — 91-prefixed,
    digits only, no +/spaces/dashes. Accepts a bare 10-digit Indian mobile
    number (what a human actually types) or one already carrying the
    country code.

    Without this, a real account could be silently missed by an exact
    match against the stored (91-prefixed) value — confirmed live: an
    admin typing their number without "91" got routed into the student
    signup flow instead of being recognized as a teacher, since
    "9031003985" != "919031003985" as a plain string. Applied at every
    login/OTP entry point (see app.routers.student_app, admin, parent_app),
    not just the public registration form it originated in.
    """
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 10:
        return f"91{digits}"
    return digits
