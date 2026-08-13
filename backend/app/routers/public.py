import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core import Student
from app.services import cost_tracker, tenancy
from app.services.otp import LOGIN_OTP_TEMPLATE_NAME, generate_and_store_otp, verify_otp
from app.services.phone import normalize_phone
from app.services.rate_limit import is_otp_rate_limited, is_signup_rate_limited, student_lock
from app.services.whatsapp_client import send_template_message, send_whatsapp_message

router = APIRouter()
logger = logging.getLogger(__name__)

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
    # No field had a max_length before — an attacker could POST a
    # multi-MB name/school string, stored straight into an unbounded
    # Postgres Text column and interpolated verbatim into an outgoing
    # WhatsApp template send. These caps are generous for any real name/
    # school/class value, just closing the unbounded-storage/DoS gap.
    name: str = Field(max_length=200)
    phone: str = Field(max_length=20)
    school: str | None = Field(default=None, max_length=200)
    student_class: str | None = Field(default=None, max_length=50)


class VerifyRegisterRequest(RegisterRequest):
    otp: str


def _validate_registration(body: RegisterRequest) -> tuple[str, str] | None:
    """Shared normalization for both steps below. Returns (name, phone), or
    None if the phone doesn't look like a real 10-digit Indian mobile
    number."""
    name = body.name.strip() or "New Student"
    phone = normalize_phone(body.phone)
    if len(phone) < 12:  # "91" + 10 digits
        return None
    return name, phone


@router.post("/public/register")
async def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """
    Step 1 of self-signup — sends a WhatsApp OTP; does NOT create the
    student or grant trial credit yet (see /public/register/verify for
    that). Confirmed live (code review, Aug 2026): this endpoint used to
    grant a real ₹50 trial credit purely on submitting a phone-shaped
    string, before any delivery/ownership confirmation at all, on top of
    having no rate limit — a script could mint unlimited accounts. The
    per-IP rate limit added earlier this audit bounds the volume; this OTP
    step closes the "credit granted before we've confirmed anyone real is
    behind that number" gap, and — since the actual student row is only
    created in the locked critical section of /verify below — also closes
    the race where two near-simultaneous submissions of the same phone
    number could each create their own account and credit grant.
    """
    client_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    if await is_signup_rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="Too many signup attempts — please try again in a few minutes")

    validated = _validate_registration(body)
    if validated is None:
        return {"success": False, "error": "Please enter a valid 10-digit WhatsApp number"}
    _, phone = validated

    # Phone-only match: the form is deliberately too small to disambiguate
    # a shared family phone with more than one child (see
    # app.services.active_profile for how WhatsApp itself resolves that
    # once the student is actually chatting) — a second submission from an
    # already-registered number just re-sends the activation nudge rather
    # than starting a new OTP flow at all.
    existing = db.query(Student).filter(Student.phone == phone, Student.is_staff_profile.is_(False)).first()
    if existing:
        # A plain session message (not a template) — only deliverable if
        # this phone already has an open 24h WhatsApp session with the bot
        # (i.e. they've messaged it before). For a genuinely cold contact
        # (only ever received outbound OTP templates, never replied) this
        # silently fails to deliver — WhatsApp's own platform rule, not
        # fixable without a dedicated approved "welcome back" template.
        # Logged rather than swallowed so a real delivery failure is at
        # least visible, even though the student isn't blocked either way
        # (they still complete login via the normal OTP flow).
        result = await send_whatsapp_message(
            phone,
            f"Hi {existing.name}! You're already set up with Qlass AI Tutor — just message me here "
            f"anytime to keep learning. 🎓",
        )
        if not result.get("sent"):
            logger.warning("already-registered re-notify failed for student_id=%s: %s", existing.id, result.get("reason"))
        return {"success": True, "already_registered": True}

    if await is_otp_rate_limited("public_signup_request", phone):
        raise HTTPException(status_code=429, detail="Too many requests — please wait a while before trying again")
    otp = await generate_and_store_otp("public_signup", phone)
    result = await send_template_message(phone, LOGIN_OTP_TEMPLATE_NAME, [{"name": "1", "value": otp}])
    if not result.get("sent"):
        raise HTTPException(status_code=502, detail="Couldn't send the verification code — please try again shortly")
    return {"success": True, "otp_required": True}


@router.post("/public/register/verify")
async def register_verify(body: VerifyRegisterRequest, db: Session = Depends(get_db)):
    """Step 2 — creates the student and grants trial credit, only after the
    WhatsApp OTP from /public/register is confirmed. See that endpoint's
    docstring for why this two-step shape exists."""
    validated = _validate_registration(body)
    if validated is None:
        return {"success": False, "error": "Please enter a valid 10-digit WhatsApp number"}
    name, phone = validated

    if await is_otp_rate_limited("public_signup_verify", phone):
        raise HTTPException(status_code=429, detail="Too many attempts — please request a new code")
    if not await verify_otp("public_signup", phone, body.otp):
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    # Locked so two near-simultaneous verify calls for the same phone (e.g.
    # a double-tap, or a retried request) can't both pass the "not already
    # registered" check below before either commits — the same TOCTOU shape
    # already fixed for WhatsApp/web chat billing (see
    # app.services.rate_limit.student_lock's other callers).
    async with student_lock(phone):
        existing = db.query(Student).filter(Student.phone == phone, Student.is_staff_profile.is_(False)).first()
        if existing:
            return {"success": True, "already_registered": True}

        centre_id = tenancy.get_qlass_direct_centre_id(db)
        # A student who came through a SPECIFIC school's own link starts
        # "pending" UNLESS that school has opted into auto-approval (see
        # Centre.auto_approve_students, a school's own choice via
        # PATCH /admin/school — defaults to true, matching the behavior every
        # school already had before this workflow existed). "pending" means
        # that school's teachers get to confirm this is actually their student
        # before it counts as a real enrolled roster member (see
        # GET /admin/students/pending). The generic Qlass Direct fallback
        # below (no school link at all) has no teacher to review it against,
        # so it always stays "approved".
        approval_status = "approved"
        if body.school:
            centre = tenancy.find_centre_by_slug(db, body.school)
            if centre:
                centre_id = centre.id
                if centre.auto_approve_students is False:
                    approval_status = "pending"

        student = tenancy.create_student_profile(
            db, phone, name, centre_id, features=dict(SELF_SIGNUP_FEATURES),
            class_name=(body.student_class or "").strip() or None, approval_status=approval_status,
        )

    # send_template_message (Wati's singular /api/v1/sendTemplateMessage),
    # not send_broadcast_template — the bulk endpoint silently degrades on
    # this account (accepts the call, reports isValidWhatsAppNumber: false,
    # never delivers) even for a number confirmed to have WhatsApp. This
    # welcome message is what actually gets a brand-new student to reply
    # and start using their trial credits — confirmed live this was
    # completely unchecked, so a real delivery failure had zero trace and
    # the student was simply left with no idea what to do next.
    welcome_result = await send_template_message(
        phone, REGISTRATION_TEMPLATE_NAME,
        [
            {"name": "1", "value": student.name},
            {"name": "2", "value": f"{cost_tracker.TRIAL_CREDITS:.0f}"},
        ],
    )
    if not welcome_result.get("sent"):
        logger.error(
            "welcome-message send FAILED for new student_id=%s phone=%s: %s",
            student.id, phone, welcome_result.get("reason") or welcome_result.get("response"),
        )
    return {"success": True, "already_registered": False}
