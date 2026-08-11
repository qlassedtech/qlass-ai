from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core import Student
from app.services import cost_tracker, tenancy
from app.services.phone import normalize_phone
from app.services.whatsapp_client import send_template_message, send_whatsapp_message

router = APIRouter()

# Approved in Wati (Utility category) specifically so a brand-new signup
# who has never messaged the bot is actually guaranteed delivery —
# WhatsApp's 24h session-window policy requires a pre-approved template to
# *initiate* contact; a plain sendSessionMessage to a genuinely cold number
# isn't reliable. Body: "Welcome to Qlass AI Tutor, {{1}}! [...] we've
# added ₹{{2}} in free AI credits [...]" — {{1}}=name, {{2}}=credit amount.
REGISTRATION_TEMPLATE_NAME = "student_signup_activation"

# Unlocked, matching app.routers.whatsapp._create_new_student's reasoning —
# a prospective student landing on the public signup page is the same
# audience as a WhatsApp first-contact self-signup, and should see the
# real, full product rather than the conservative all-off default used for
# school-provisioned OTP web signups (see tenancy.DEFAULT_FEATURES).
SELF_SIGNUP_FEATURES = {"voice": True, "ocr": True, "image_generation": True, "documents": True, "youtube_videos": True}



@router.get("/public/school-info")
def get_school_info(school: str | None = None, db: Session = Depends(get_db)):
    """Lets the landing page show the school's own name and logo when
    opened through a school-specific link, without exposing anything else
    about the centre."""
    if not school:
        return {"name": None, "logo_url": None}
    centre = tenancy.find_centre_by_slug(db, school)
    if not centre:
        return {"name": None, "logo_url": None}
    return {"name": centre.name, "logo_url": centre.logo_url}


class RegisterRequest(BaseModel):
    name: str
    phone: str
    school: str | None = None
    student_class: str | None = None


@router.post("/public/register")
async def register(body: RegisterRequest, db: Session = Depends(get_db)):
    name = body.name.strip() or "New Student"
    phone = normalize_phone(body.phone)
    if len(phone) < 12:  # "91" + 10 digits
        return {"success": False, "error": "Please enter a valid 10-digit WhatsApp number"}

    centre_id = tenancy.get_qlass_direct_centre_id(db)
    # A student who came through a SPECIFIC school's own link starts
    # "pending" — that school's teachers get to confirm this is actually
    # their student before it counts as a real enrolled roster member (see
    # GET /admin/students/pending). The generic Qlass Direct fallback
    # below (no school link at all) has no teacher to review it against,
    # so it stays "approved" same as always.
    approval_status = "approved"
    if body.school:
        centre = tenancy.find_centre_by_slug(db, body.school)
        if centre:
            centre_id = centre.id
            approval_status = "pending"

    # Phone-only match: the form is deliberately too small to disambiguate
    # a shared family phone with more than one child (see
    # app.services.active_profile for how WhatsApp itself resolves that
    # once the student is actually chatting) — a second submission from an
    # already-registered number just re-sends the activation nudge rather
    # than creating a duplicate account and re-granting trial credit.
    existing = db.query(Student).filter(Student.phone == phone, Student.is_staff_profile.is_(False)).first()
    if existing:
        await send_whatsapp_message(
            phone,
            f"Hi {existing.name}! You're already set up with Qlass AI Tutor — just message me here "
            f"anytime to keep learning. 🎓",
        )
        return {"success": True, "already_registered": True}

    student = tenancy.create_student_profile(
        db, phone, name, centre_id, features=dict(SELF_SIGNUP_FEATURES),
        class_name=(body.student_class or "").strip() or None, approval_status=approval_status,
    )

    # send_template_message (Wati's singular /api/v1/sendTemplateMessage),
    # not send_broadcast_template — the bulk endpoint silently degrades on
    # this account (accepts the call, reports isValidWhatsAppNumber: false,
    # never delivers) even for a number confirmed to have WhatsApp. This
    # welcome message is what actually gets a brand-new student to reply
    # and start using their trial credits, so a swallowed failure here
    # means a real signup with no idea what to do next.
    await send_template_message(
        phone, REGISTRATION_TEMPLATE_NAME,
        [
            {"name": "1", "value": student.name},
            {"name": "2", "value": f"{cost_tracker.TRIAL_CREDITS:.0f}"},
        ],
    )
    return {"success": True, "already_registered": False}
