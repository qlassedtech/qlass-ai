from sqlalchemy.orm import Session

from app.models.core import Centre, Student
from app.services import cost_tracker
from app.services.referral import generate_referral_code
from app.services.text_utils import tokenize_words

# A student who signs up directly (via WhatsApp or the web app) without any
# school having enrolled them first still needs a tenant to belong to —
# every admin view/query in admin.py scopes by centre_id. They land under
# this Qlass-owned catch-all "school" rather than a null centre_id that no
# admin console could ever see. Seeded once via migration 0020.
QLASS_DIRECT_CENTRE_NAME = "Qlass Direct"
_qlass_direct_centre_id: int | None = None

DEFAULT_FEATURES = {"voice": False, "ocr": False, "image_generation": False, "documents": False, "youtube_videos": False}


def get_qlass_direct_centre_id(db: Session) -> int | None:
    global _qlass_direct_centre_id
    if _qlass_direct_centre_id is None:
        centre = db.query(Centre).filter(Centre.name == QLASS_DIRECT_CENTRE_NAME).first()
        _qlass_direct_centre_id = centre.id if centre else None
    return _qlass_direct_centre_id


def slugify_centre_name(name: str) -> str:
    """
    URL/text-friendly form of a centre name (e.g. "Sunrise Public School"
    -> "sunrise-public-school") — shared by the public landing-page link
    (app.routers.public) and the WhatsApp click-to-chat link (app.routers.
    whatsapp's "Hi school:<slug>" pre-filled greeting), so a school gets
    exactly one identifier regardless of which channel's link it uses.
    Reuses the existing plain-character-scan tokenizer rather than a
    regex, same as every other text-processing helper in this codebase.
    """
    return "-".join(tokenize_words(name))


def find_centre_by_slug(db: Session, slug: str) -> Centre | None:
    """
    Confirmed live (real school onboarding, Aug 2026) this is a genuine
    production bug, not just a theoretical edge case: two schools named
    "Leed's Asian School" existed (one a stale leftover from unrelated
    earlier testing, one the school's own real, current registration) —
    both slugify identically, and the old plain `db.query(Centre).all()`
    iteration order (effectively insertion/id order) always matched the
    OLDER one first, silently routing a real self-signed-up student onto
    a dead/abandoned centre the school's own admin has never seen. There's
    still no way to fully disambiguate two DIFFERENT real schools sharing
    a name (see /auth/register-school's own docstring on why that isn't
    hard-blocked), but "prefer the most recently active, most recently
    created match" is a far better default than "prefer the oldest row in
    the table" for the overwhelmingly common real case — a stale/abandoned
    duplicate registration losing out to a real, current one.
    """
    slug = slug.lower()
    matches = [c for c in db.query(Centre).all() if slugify_centre_name(c.name) == slug]
    if not matches:
        return None
    matches.sort(key=lambda c: (c.sales_status == "active", c.created_at), reverse=True)
    return matches[0]


def default_board_for_centre(db: Session, centre_id: int | None) -> str | None:
    """
    A school already knows which board it follows — a new student under it
    should default to that instead of being asked individually (see
    profile_builder.PROFILE_QUESTIONS, which only asks when the field is
    still genuinely empty).
    """
    if centre_id is None:
        return None
    centre = db.query(Centre).filter(Centre.id == centre_id).first()
    return centre.board if centre else None


def default_school_for_centre(db: Session, centre_id: int | None) -> str | None:
    """
    Same idea as default_board_for_centre — a student enrolled through a
    real school's own teacher/admin already IS at that school, so
    Student.school shouldn't sit blank asking to be filled in separately.
    Explicitly excludes Qlass Direct (the internal bucket for self-
    registered/independent students with no real school on file) — that
    name is a Qlass-internal label, not a real school, so it must never be
    written into a student's own "school" field.
    """
    if centre_id is None:
        return None
    centre = db.query(Centre).filter(Centre.id == centre_id).first()
    if not centre or centre.name == QLASS_DIRECT_CENTRE_NAME:
        return None
    return centre.name


def create_student_profile(
    db: Session, phone: str, name: str, centre_id: int | None, is_staff_profile: bool = False,
    features: dict | None = None, class_name: str | None = None, approval_status: str = "approved",
) -> Student:
    """Shared by the student web app's OTP signup, a teacher's own
    lazily-created personal tutor profile (see app.routers.student_app and
    the /admin/my-tutor endpoints), and the public landing-page self-
    registration form (see app.routers.public). `features` defaults to the
    conservative DEFAULT_FEATURES (all off) used for school-provisioned
    signups — pass an explicit dict for a self-signup channel that should
    unlock everything, same as app.routers.whatsapp's own first-contact
    flow. `approval_status` is "pending" only for a student self-
    registering through a specific school's link (see
    app.routers.public.register) — every other caller leaves it at the
    default "approved"."""
    student = Student(
        name=name, phone=phone, features=dict(features) if features is not None else dict(DEFAULT_FEATURES),
        centre_id=centre_id, is_staff_profile=is_staff_profile, board=default_board_for_centre(db, centre_id),
        school=default_school_for_centre(db, centre_id), class_=class_name, approval_status=approval_status,
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    student.referral_code = generate_referral_code(student.id)
    db.commit()
    cost_tracker.add_trial_credits(db, student.id)
    return student


def get_or_create_linked_student(db: Session, phone: str, name: str, centre_id: int | None) -> Student:
    """
    A teacher's OWN personal tutor profile — matched on is_staff_profile
    specifically (not just phone+centre), so this can never find or merge
    into a REAL enrolled student who happens to share the teacher's own
    phone number (e.g. a teacher who is also a parent at the same school).
    """
    student = (
        db.query(Student)
        .filter(Student.phone == phone, Student.centre_id == centre_id, Student.is_staff_profile.is_(True))
        .order_by(Student.id)
        .first()
    )
    return student or create_student_profile(db, phone, name, centre_id, is_staff_profile=True)
