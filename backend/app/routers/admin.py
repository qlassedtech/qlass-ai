import csv
import io
import secrets
from datetime import datetime, timedelta, timezone

import httpx
import razorpay
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, Query as QueryParam
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.query import Query

from app.business_rules import TUTOR_LEVEL_MODELS
from app.config import settings
from app.database import get_db
from app.models.core import AuditLog, Centre, Chapter, ChatHistory, CreditEvent, Parent, Question, Quiz, Student, Subject, Teacher
from app.services import audit_log, cost_tracker, school_billing
from app.services.escalation import QLASS_SUPPORT_PHONE, SCHOOL_REVIEW_STAFF_PHONES, get_escalation_recipients
from app.services.school_pilot import PILOT_STUDENT_FEATURES, MAX_PILOT_STUDENTS, launch_pilot, pilot_outcome_report
from app.services.analytics import get_school_analytics
from app.services.deletion import fulfill_deletion_request
from app.services.otp import generate_and_store_otp, verify_otp, LOGIN_OTP_TEMPLATE_NAME
from app.services.google_auth import GoogleAuthError, verify_google_id_token
from app.services.phone import normalize_phone
from app.services.quiz_service import generate_quiz_questions
from app.services.sales import get_schools_overview
from app.services.school_statement import generate_school_statement_pdf
from app.services.rate_limit import is_otp_rate_limited, is_signup_rate_limited, student_lock
from app.services.pdf_render import render_workbook_pdf
from app.services.progress_report import get_student_stats, get_activity_stats, get_chapter_coverage, format_teacher_digest
from app.services.gamma_service import create_presentation_generation, get_generation_status
from app.services import razorpay_client
from app.services.razorpay_client import client as _razorpay_client, MIN_TOPUP_AMOUNT
from app.services.student_chat import process_web_message
from app.services.teacher_auth import get_current_teacher, hash_password, verify_password, create_access_token
from app.services import tenancy
from app.services.tenancy import get_or_create_linked_student
from app.services.uploads import save_image_upload
from app.services.whatsapp_client import send_whatsapp_message, send_template_message
from app.services.workbook_service import generate_workbook_questions
from app.services.ocr_client import extract_text_from_image
from app.services.document_client import extract_text_from_document
from app.services.sarvam_client import transcribe_audio
from app.services.audio_qa import detect_gender_from_pitch, get_duration_seconds
from app.services.roster_extraction import extract_student_rows, extract_teacher_rows

router = APIRouter()

DEFAULT_FEATURES = {"voice": False, "ocr": False, "image_generation": False, "documents": False, "youtube_videos": False}

# Same rationale as student_app.py's CHAT_HISTORY_LIMIT — a long-running
# "My AI Tutor" chat shouldn't return an ever-growing unbounded history.
MY_TUTOR_CHAT_HISTORY_LIMIT = 200

# Applied everywhere a teacher/admin password is SET (register-school,
# create-teacher, bulk teacher upload, reset-password) — previously only
# set_student_password enforced this, so a school admin's own login
# (unlike a student's) could be a single character, trivially guessable.
MIN_PASSWORD_LENGTH = 6


def _require_valid_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters")


# Bulk roster/teacher upload — see _rows_from_roster_upload and the
# confirm endpoints below. Each created row grants a real ₹50 trial credit
# (students) or a real login-capable account (teachers), so both the raw
# upload and the confirm step (which takes its own `rows` directly from the
# client, not necessarily the same ones just previewed) need a ceiling.
MAX_ROSTER_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB, matching app.services.uploads.MAX_IMAGE_BYTES
MAX_ROSTER_UPLOAD_ROWS = 2000


class LoginRequest(BaseModel):
    phone: str
    password: str


class StudentCreateRequest(BaseModel):
    name: str
    phone: str
    class_: str | None = None
    board: str | None = None
    school: str | None = None


class StudentUpdateRequest(BaseModel):
    name: str | None = None
    phone: str | None = None
    class_: str | None = None
    board: str | None = None
    school: str | None = None
    # Only ever auto-detected from voice-note pitch today (see
    # app.services.audio_qa) — that heuristic is admittedly error-prone,
    # and until now a teacher had no way to see or correct a wrong guess,
    # which picks the wrong opposite-gender TTS voice on every future
    # voice reply for that student.
    gender: str | None = None
    focus_topic: str | None = None
    features: dict | None = None
    parent_phone: str | None = None
    parent_name: str | None = None
    # An alternate real WhatsApp number — set when the student's primary
    # `phone` above isn't itself on WhatsApp. See Student.whatsapp_phone.
    whatsapp_phone: str | None = None


def _student_to_dict(
    db: Session, student: Student, balance: float | None = None, referral_credits: float | None = None,
    parent: Parent | None | bool = False,
) -> dict:
    """
    balance/referral_credits/parent are optional pre-fetched values — pass
    them when serializing a whole page of students (see list_students,
    which batch-fetches all three in 3 queries total instead of each
    student here re-querying credit_events/parents individually; confirmed
    live via a scale audit that a 500-row page was issuing ~1500 queries
    and taking 1.5-2.3s before this). `parent` uses False (not None) as its
    "not supplied" sentinel since None is itself a valid pre-fetched
    "no parent linked" result.
    """
    if balance is None:
        balance = cost_tracker.get_balance(db, student.id)
    if referral_credits is None:
        referral_credits = cost_tracker.get_referral_credits_earned(db, student.id)
    if parent is False:
        parent = db.query(Parent).filter(Parent.student_id == student.id).first()
    return {
        "id": student.id,
        "name": student.name,
        "phone": student.phone,
        "class": student.class_,
        "board": student.board,
        "school": student.school,
        "gender": student.gender,
        "focus_topic": student.focus_topic,
        "features": student.features or {},
        "hints_given_count": student.hints_given_count or 0,
        "direct_solutions_count": student.direct_solutions_count or 0,
        "credit_balance": balance,
        "centre_id": student.centre_id,
        "referral_code": student.referral_code,
        "referral_credits_earned": referral_credits,
        "photo_url": student.photo_url,
        "parent_phone": parent.phone if parent else None,
        "parent_name": parent.name if parent else None,
        "subscription_plan": student.subscription_plan,
        "subscription_expires_at": student.subscription_expires_at,
        "whatsapp_phone": student.whatsapp_phone,
        "has_password": student.password_hash is not None,
        "approval_status": student.approval_status or "approved",
    }


def _org_centre_ids(db: Session, organization_id: int | None):
    """Every centre_id under one organization — see the Organization model."""
    return db.query(Centre.id).filter(Centre.organization_id == organization_id)


def _scoped_students(db: Session, teacher: Teacher) -> Query:
    """
    This product is sold to multiple schools — a school's own admin/teacher
    must only ever see their own school's (centre's) students, never
    another school's. "org_admin" sees across every centre under their own
    organization_id (e.g. a government programme spanning many schools) —
    see the Organization model. Only "super_admin" (Qlass's own staff) sees
    across every school on the whole platform. Also excludes teachers' own
    personal "My AI Tutor" profiles (see tenancy.get_or_create_linked_student)
    — those aren't real students and shouldn't appear in any student-facing
    list. And excludes students whose data-deletion request has been
    fulfilled (see app.services.deletion) — that row is kept (anonymized)
    only for the credit_events audit trail, not to be shown as a live student.
    """
    query = db.query(Student).filter(Student.is_staff_profile.is_(False), Student.is_deleted.is_(False))
    if teacher.role == "super_admin":
        return query
    if teacher.role == "org_admin":
        return query.filter(Student.centre_id.in_(_org_centre_ids(db, teacher.organization_id)))
    return query.filter(Student.centre_id == teacher.centre_id)


def _require_school_management_access(teacher: Teacher, centre: Centre) -> None:
    """
    Shared gate for school-level bulk actions (trial grants, pilot launch)
    that used to be super_admin-only — an org_admin may perform the same
    actions, but only for a school inside their own organization, never an
    unrelated school on the platform.
    """
    if teacher.role == "super_admin":
        return
    if teacher.role == "org_admin" and centre.organization_id == teacher.organization_id:
        return
    raise HTTPException(status_code=403, detail="You don't have management access to this school")


def _get_scoped_student_or_404(db: Session, teacher: Teacher, student_id: int) -> Student:
    student = _scoped_students(db, teacher).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


@router.post("/auth/login")
async def login(body: LoginRequest, db: Session = Depends(get_db)):
    phone = normalize_phone(body.phone)
    # Unlike the OTP flows below, this endpoint previously had NO rate
    # limiting at all — confirmed live an attacker could brute-force a
    # known/guessed teacher/admin/super_admin phone's password with
    # unlimited attempts. Same 5-attempts/10-minutes budget as OTP verify,
    # keyed by the target phone (not the caller's IP) so an attacker can't
    # reset their budget by rotating source IPs.
    if await is_otp_rate_limited("teacher_login_password", phone):
        raise HTTPException(status_code=429, detail="Too many login attempts — please wait a few minutes and try again")
    teacher = db.query(Teacher).filter(Teacher.phone == phone).first()
    if not teacher or not teacher.password_hash or not verify_password(body.password, teacher.password_hash):
        raise HTTPException(status_code=401, detail="Invalid phone or password")
    token = create_access_token(teacher.id, teacher.token_version or 0)
    return {"access_token": token, "teacher": {"id": teacher.id, "name": teacher.name, "role": teacher.role}}


class GoogleLoginRequest(BaseModel):
    id_token: str


@router.post("/auth/google-login")
def google_login(body: GoogleLoginRequest, db: Session = Depends(get_db)):
    """
    Sign in with an already-linked Google account (see google_id_token on
    /auth/register-school, which is how the link gets created in the first
    place — this endpoint never creates a new account, same reasoning as
    /auth/request-teacher-otp not creating one for OTP login). No rate
    limiting needed here the way password login has: the "secret" being
    checked is a cryptographically signed token from Google, not a
    guessable credential — there's no brute-force surface to protect
    against.
    """
    try:
        payload = verify_google_id_token(body.id_token)
    except GoogleAuthError as exc:
        raise HTTPException(status_code=401, detail=f"Google sign-in failed: {exc}")
    if not payload.get("email_verified"):
        raise HTTPException(status_code=401, detail="Your Google account's email isn't verified")
    teacher = db.query(Teacher).filter(Teacher.email == payload["email"]).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="No account found for this Google email — register your school first")
    token = create_access_token(teacher.id, teacher.token_version or 0)
    return {"access_token": token, "teacher": {"id": teacher.id, "name": teacher.name, "role": teacher.role}}


class TeacherPhoneRequest(BaseModel):
    phone: str


@router.post("/auth/request-teacher-otp")
async def request_teacher_otp(body: TeacherPhoneRequest, db: Session = Depends(get_db)):
    """
    WhatsApp OTP login for teacher/admin accounts — sits ALONGSIDE password
    login (see /auth/login above), not a replacement; a teacher who forgets
    their password, or just prefers it, can use this instead. Unlike the
    student OTP flow, this never creates a new account — a teacher/admin
    row must already exist (via /auth/register-school or an admin adding
    them), since OTP-only signup for a portal role with real permissions
    would let anyone claim an account just by knowing a phone number.
    """
    phone = normalize_phone(body.phone)
    if await is_otp_rate_limited("teacher_login_request", phone):
        raise HTTPException(status_code=429, detail="Too many requests — please wait a while before trying again")
    teacher = db.query(Teacher).filter(Teacher.phone == phone).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="No teacher/admin account found for this number")
    otp = await generate_and_store_otp("teacher_login", phone)
    # A teacher logging into the web portal cold has no guaranteed active
    # 24h WhatsApp session with the bot — a plain send_whatsapp_message
    # session message silently fails to deliver in that case (confirmed
    # live: it reported {"sent": True} while nothing actually arrived), so
    # this must use the approved Authentication-category template instead.
    # Must be send_template_message (singular endpoint), not
    # send_broadcast_template — see that function's docstring; the bulk
    # endpoint silently degrades on this account even with the same
    # template/number.
    result = await send_template_message(phone, LOGIN_OTP_TEMPLATE_NAME, [{"name": "1", "value": otp}])
    if not result.get("sent"):
        raise HTTPException(status_code=502, detail="Couldn't send the login code — please try again shortly")
    return {"sent": True}


class VerifyTeacherOtpRequest(BaseModel):
    phone: str
    otp: str


@router.post("/auth/verify-teacher-otp")
async def verify_teacher_otp(body: VerifyTeacherOtpRequest, db: Session = Depends(get_db)):
    phone = normalize_phone(body.phone)
    if await is_otp_rate_limited("teacher_login_verify", phone):
        raise HTTPException(status_code=429, detail="Too many attempts — please request a new code")
    if not await verify_otp("teacher_login", phone, body.otp):
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    teacher = db.query(Teacher).filter(Teacher.phone == phone).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="No teacher/admin account found for this number")
    token = create_access_token(teacher.id, teacher.token_version or 0)
    return {"access_token": token, "teacher": {"id": teacher.id, "name": teacher.name, "role": teacher.role}}


# Approved in Wati (Utility category) with two Quick Reply buttons —
# "Approve School" / "Reject School" — matching app.routers.whatsapp.
# SCHOOL_REVIEW_BUTTON_ACTIONS exactly. Body params in order: school name,
# city, admin name, admin phone, centre_id, duplicate-name warning (blank
# if none).
SCHOOL_REVIEW_TEMPLATE_NAME = "school_onboarding_review_alert"


class RegisterSchoolRequest(BaseModel):
    school_name: str = Field(max_length=200)
    city: str | None = Field(default=None, max_length=100)
    # Confirmed live (real school onboarding, Aug 2026): a self-registered
    # school never had anywhere to set this, so tenancy.
    # default_board_for_centre had nothing to default new students to —
    # every single student joining through this school's own /join link
    # then got asked "which board?" in chat, even though a school always
    # already knows its own board. Optional (a school can still set/fix it
    # later via PATCH /admin/school) so this doesn't block registration.
    board: str | None = Field(default=None, max_length=40)
    admin_name: str = Field(max_length=200)
    admin_phone: str = Field(max_length=20)
    # Exactly one of these two must be given — a password (the original
    # flow) or a Google ID token (see app.services.google_auth), letting
    # the new admin sign in with Google instead of setting/remembering a
    # portal password. Both remain valid ongoing login methods once set;
    # this only decides which one is set up at registration time.
    password: str | None = None
    google_id_token: str | None = None


@router.post("/auth/register-school")
async def register_school(body: RegisterSchoolRequest, request: Request, db: Session = Depends(get_db)):
    """
    Step 1 of self-serve school registration — sends a WhatsApp OTP to the
    admin's phone; does NOT create the school or admin account yet (see
    /auth/register-school/verify for that).

    Confirmed live this had NO identity verification of any kind — every
    other account-creation endpoint (/public/register, WhatsApp cold-start)
    requires OTP-proven phone ownership before an account exists, but this
    one created a fully-privileged school-admin account for ANY phone
    number on the strength of the request body alone, with zero rate
    limiting either. Someone could register a school admin account keyed to
    a real person's phone number without them ever proving — or even
    knowing — they control it.
    """
    client_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    if await is_signup_rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="Too many school registrations from this network — please try again later")
    admin_phone = normalize_phone(body.admin_phone)
    if db.query(Teacher).filter(Teacher.phone == admin_phone).first():
        raise HTTPException(status_code=409, detail="An account with this phone number already exists")
    if await is_otp_rate_limited("register_school_request", admin_phone):
        raise HTTPException(status_code=429, detail="Too many requests — please wait a while before trying again")
    otp = await generate_and_store_otp("register_school", admin_phone)
    result = await send_template_message(admin_phone, LOGIN_OTP_TEMPLATE_NAME, [{"name": "1", "value": otp}])
    if not result.get("sent"):
        raise HTTPException(status_code=502, detail="Couldn't send the verification code — please try again shortly")
    return {"otp_required": True}


class VerifyRegisterSchoolRequest(RegisterSchoolRequest):
    otp: str


@router.post("/auth/register-school/verify")
async def register_school_verify(body: VerifyRegisterSchoolRequest, db: Session = Depends(get_db)):
    """
    Step 2 — creates the school (Centre, the tenant boundary) and its first
    admin account in one step, since a brand-new school has no existing
    admin to invite them, only after the WhatsApp OTP from
    /auth/register-school is confirmed. Any additional teacher/admin
    accounts for this school are added afterward from the portal (see POST
    /admin/teachers), not through this endpoint again.
    """
    admin_phone = normalize_phone(body.admin_phone)
    if await is_otp_rate_limited("register_school_verify", admin_phone):
        raise HTTPException(status_code=429, detail="Too many attempts — please request a new code")
    if not await verify_otp("register_school", admin_phone, body.otp):
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    if bool(body.password) == bool(body.google_id_token):
        raise HTTPException(status_code=400, detail="Provide exactly one of password or google_id_token")
    google_email = None
    if body.google_id_token:
        try:
            payload = verify_google_id_token(body.google_id_token)
        except GoogleAuthError as exc:
            raise HTTPException(status_code=401, detail=f"Google sign-in failed: {exc}")
        if not payload.get("email_verified"):
            raise HTTPException(status_code=401, detail="Your Google account's email isn't verified")
        google_email = payload["email"]
        if db.query(Teacher).filter(Teacher.email == google_email).first():
            raise HTTPException(status_code=409, detail="An account with this Google email already exists")
    else:
        _require_valid_password(body.password)

    # Locked so two near-simultaneous verify calls for the same phone can't
    # both pass this check before either commits — same TOCTOU shape fixed
    # elsewhere for student signup (see app.services.rate_limit.student_lock's
    # other callers).
    async with student_lock(admin_phone):
        if db.query(Teacher).filter(Teacher.phone == admin_phone).first():
            raise HTTPException(status_code=409, detail="An account with this phone number already exists")

        # Confirmed live: this endpoint had zero identity verification — anyone
        # could register a school under a name matching (or closely mimicking) a
        # real partner school's, then have it silently resolved as that school by
        # find_centre_by_slug (used by the public landing page and WhatsApp
        # first-contact school-name matching), intercepting signups meant for
        # the real school.
        #
        # Deliberately NOT a hard block, even on name+city together — both a
        # school name (e.g. "Delhi Public School", "Saraswati Vidya Mandir")
        # AND a city name can genuinely recur across unrelated parts of India
        # with no relationship to each other, so any string-match rule here
        # risks locking out a real school on a false positive, which is worse
        # than the spoofing risk it's meant to catch. Instead this is surfaced
        # as a flag on the review notification below — a human (who can ask
        # "which district, which board, is this really the same school?")
        # makes the call, not a string comparison.
        name_matches = (
            db.query(Centre).filter(func.lower(Centre.name) == body.school_name.strip().lower()).all()
        )

        centre = Centre(name=body.school_name, city=body.city, board=body.board, sales_status="prospect")
        db.add(centre)
        db.commit()
        db.refresh(centre)
        school_billing.add_trial_credits(db, centre.id)

        teacher = Teacher(
            name=body.admin_name, phone=admin_phone, email=google_email,
            password_hash=hash_password(body.password) if body.password else None,
            role="admin", centre_id=centre.id,
        )
        db.add(teacher)
        db.commit()

    # Self-registered schools start "prospect" (not the model's normal
    # "active" default) specifically so they're visibly distinct from a
    # Qlass-vetted school in every existing sales-pipeline view (see
    # app.services.sales) until a real human reviews and promotes them via
    # PATCH /admin/school — this doesn't block the new admin's own portal
    # access (the self-serve value prop stays intact), it just leaves a
    # review trail where there was previously none at all. Best-effort: a
    # missed notification shouldn't block a legitimate school's signup.
    # Confirmed live: Wati rejects a template send with a blank/empty
    # parameter value ("Check your template, it cannot have typos or blank
    # text") — every {{n}} placeholder needs a real, non-empty string.
    duplicate_note = "No duplicate-name conflicts found."
    if name_matches:
        other_cities = ", ".join(c.city or "city not given" for c in name_matches)
        duplicate_note = (
            f"⚠️ {len(name_matches)} existing school(s) already use this exact name (city: "
            f"{other_cities}) — confirm this isn't a duplicate/impersonation before approving."
        )
    template_params = [
        {"name": "1", "value": body.school_name},
        {"name": "2", "value": body.city or "city not given"},
        {"name": "3", "value": body.admin_name},
        {"name": "4", "value": admin_phone},
        {"name": "5", "value": str(centre.id)},
        {"name": "6", "value": duplicate_note},
    ]
    # Sent to each Qlass staff number in SCHOOL_REVIEW_STAFF_PHONES — a
    # template (not send_whatsapp_message) since staff numbers have no
    # guaranteed open 24h session, and its attached Quick Reply buttons
    # (see app.routers.whatsapp.SCHOOL_REVIEW_BUTTON_ACTIONS) let a
    # reviewer approve/reject straight from WhatsApp, no portal login
    # needed for the common case. Best-effort: a missed notification
    # shouldn't block a legitimate school's signup.
    for staff_phone in SCHOOL_REVIEW_STAFF_PHONES:
        try:
            await send_template_message(staff_phone, SCHOOL_REVIEW_TEMPLATE_NAME, template_params)
        except Exception:
            pass
    db.refresh(teacher)

    token = create_access_token(teacher.id, teacher.token_version or 0)
    return {"access_token": token, "teacher": {"id": teacher.id, "name": teacher.name, "role": teacher.role}}


class ForgotPasswordRequest(BaseModel):
    phone: str


@router.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    if await is_otp_rate_limited("password_reset_request", body.phone):
        raise HTTPException(status_code=429, detail="Too many requests — please wait a while before trying again")
    teacher = db.query(Teacher).filter(Teacher.phone == body.phone).first()
    # Always return the same response whether or not this phone has an
    # account — a different response would let someone probe which phone
    # numbers have portal logins.
    if teacher:
        otp = await generate_and_store_otp("password_reset", body.phone)
        await send_whatsapp_message(
            body.phone, f"Your Qlass Learning password reset code is *{otp}*. It expires in 10 minutes."
        )
    return {"sent": True}


class ResetPasswordRequest(BaseModel):
    phone: str
    otp: str
    new_password: str


@router.post("/auth/reset-password")
async def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    if await is_otp_rate_limited("password_reset_verify", body.phone):
        raise HTTPException(status_code=429, detail="Too many attempts — please request a new code")
    if not await verify_otp("password_reset", body.phone, body.otp):
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    _require_valid_password(body.new_password)
    teacher = db.query(Teacher).filter(Teacher.phone == body.phone).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Account not found")
    teacher.password_hash = hash_password(body.new_password)
    # Invalidates every token issued before this reset — see
    # Teacher.token_version / get_current_teacher's docstring. Without this,
    # a stolen token kept working for up to 7 more days after the standard
    # incident-response move of resetting the password.
    teacher.token_version = (teacher.token_version or 0) + 1
    db.commit()
    return {"reset": True}


@router.get("/admin/me")
def me(teacher: Teacher = Depends(get_current_teacher)):
    return {
        "id": teacher.id, "name": teacher.name, "role": teacher.role, "phone": teacher.phone,
        "photo_url": teacher.photo_url, "centre_id": teacher.centre_id, "organization_id": teacher.organization_id,
    }


@router.get("/admin/students")
def list_students(
    limit: int = QueryParam(default=100, ge=1, le=500),
    offset: int = QueryParam(default=0, ge=0),
    db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    students = _scoped_students(db, teacher).order_by(Student.id).offset(offset).limit(limit).all()
    student_ids = [s.id for s in students]

    # Batched — 3 queries total for the whole page instead of 3 per
    # student (see _student_to_dict's docstring for the scale-audit finding
    # this replaces).
    balances = dict(
        db.query(CreditEvent.student_id, func.coalesce(func.sum(CreditEvent.amount), 0))
        .filter(CreditEvent.student_id.in_(student_ids))
        .group_by(CreditEvent.student_id)
        .all()
    )
    referral_credits = dict(
        db.query(CreditEvent.student_id, func.coalesce(func.sum(CreditEvent.amount), 0))
        .filter(CreditEvent.student_id.in_(student_ids), CreditEvent.service == cost_tracker.REFERRAL_BONUS_SERVICE)
        .group_by(CreditEvent.student_id)
        .all()
    )
    parents = {p.student_id: p for p in db.query(Parent).filter(Parent.student_id.in_(student_ids)).all()}

    return [
        _student_to_dict(
            db, s, balance=float(balances.get(s.id, 0)), referral_credits=float(referral_credits.get(s.id, 0)),
            parent=parents.get(s.id),
        )
        for s in students
    ]


@router.get("/admin/students/pending")
def list_pending_students(db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)):
    """
    Students who self-registered through this school's own link (see
    POST /public/register, app.services.tenancy.create_student_profile's
    approval_status parameter) and haven't been confirmed by a teacher yet
    — a separate, smaller list from the main roster (GET /admin/students)
    so a school can review "is this really our student?" before treating
    a self-signup as a real enrolled member, without it getting lost among
    everyone else.
    """
    students = (
        _scoped_students(db, teacher)
        .filter(Student.approval_status == "pending")
        .order_by(Student.id.desc())
        .all()
    )
    return [_student_to_dict(db, s) for s in students]


@router.post("/admin/students/{student_id}/approve")
def approve_student(
    student_id: int, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    student = _get_scoped_student_or_404(db, teacher, student_id)
    student.approval_status = "approved"
    db.commit()
    return _student_to_dict(db, student)


@router.post("/admin/students")
def create_student(
    body: StudentCreateRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    student = Student(
        name=body.name, phone=body.phone, class_=body.class_,
        board=body.board or tenancy.default_board_for_centre(db, teacher.centre_id),
        school=body.school or tenancy.default_school_for_centre(db, teacher.centre_id),
        features=dict(DEFAULT_FEATURES), centre_id=teacher.centre_id,
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    cost_tracker.add_trial_credits(db, student.id)
    return _student_to_dict(db, student)


async def _rows_from_roster_upload(file: UploadFile, extract_rows_fn, db: Session, bill_centre_id: int | None) -> list[dict]:
    """
    Turns an uploaded file into row dicts (string keys like name/phone/...)
    regardless of source format — a clean CSV parses directly; a roster
    photo or PDF/Word file goes through OCR/document-text extraction and
    then an LLM extraction pass (see app.services.roster_extraction).

    Always returns a PREVIEW only, never writes anything — OCR/LLM
    extraction from a photo or scanned document is not reliable enough to
    create real accounts unattended; a human must review (and can correct)
    these rows before the separate .../confirm endpoint actually acts on
    them.
    """
    content_type = (file.content_type or "").lower()
    filename = (file.filename or "").lower()
    raw_bytes = await file.read()
    # No cap existed here before — unlike save_image_upload's MAX_IMAGE_
    # BYTES, an admin (already an authenticated, school-scoped account, but
    # still worth bounding) could upload an arbitrarily large file and have
    # it fully buffered in memory before any row-count check downstream.
    if len(raw_bytes) > MAX_ROSTER_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=f"File too large — max {MAX_ROSTER_UPLOAD_BYTES // (1024 * 1024)}MB")

    if filename.endswith(".csv") or content_type in ("text/csv", "application/vnd.ms-excel"):
        reader = csv.DictReader(io.StringIO(raw_bytes.decode("utf-8-sig")))
        reader.fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]
        rows = [dict(row) for row in reader]
        if len(rows) > MAX_ROSTER_UPLOAD_ROWS:
            raise HTTPException(status_code=400, detail=f"Too many rows in one upload (max {MAX_ROSTER_UPLOAD_ROWS}) — split into batches")
        return rows

    if content_type.startswith("image/"):
        source_text = await extract_text_from_image(raw_bytes)
    elif filename.endswith((".pdf", ".docx")):
        source_text = extract_text_from_document(raw_bytes, filename)
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type — use a CSV, image, PDF, or Word file")

    if not source_text:
        raise HTTPException(status_code=400, detail="Couldn't read any text from that file — try a clearer photo/scan")

    rows, llm_result = await extract_rows_fn(source_text)
    if bill_centre_id is not None:
        school_billing.record_claude_usage(
            db, bill_centre_id, "roster_extraction", llm_result.input_tokens, llm_result.output_tokens,
        )
    return rows


@router.post("/admin/students/bulk-upload/preview")
async def preview_student_bulk_upload(
    file: UploadFile = File(...), centre_id: int | None = None,
    db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    """
    Parses a CSV, or OCRs/extracts a roster photo, PDF, or Word file, into
    row previews for the admin to review — and correct — before anything is
    created. Expected fields per row: name, phone, class, board, school
    (only name/phone are actually required to proceed). `centre_id` is
    required for org_admin/super_admin (who have no single school of their
    own — see _resolve_centre_for_write) and ignored for a school's own
    admin, who always uploads into their own school.
    """
    centre = _resolve_centre_for_write(db, teacher, centre_id)
    rows = await _rows_from_roster_upload(file, extract_student_rows, db, centre.id)
    return {"rows": rows}


class ConfirmStudentBulkUploadRequest(BaseModel):
    # max_length: this is the actual create step (unlike preview above, it
    # doesn't require rows to have come from a preview call at all) — each
    # row grants a real ₹50 trial credit, so it needs its own ceiling.
    rows: list[dict] = Field(max_length=MAX_ROSTER_UPLOAD_ROWS)
    features: dict[str, bool] = {}
    centre_id: int | None = None


@router.post("/admin/students/bulk-upload/confirm")
def confirm_student_bulk_upload(
    body: ConfirmStudentBulkUploadRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    """
    Actually creates/updates students from previously-previewed (and
    possibly hand-corrected) rows — see .../bulk-upload/preview. The same
    feature-access selection is applied to every student in the batch —
    for per-student feature differences, upload separate batches or adjust
    individually afterward on the student's detail page. Every created
    student is assigned to the resolved school (centre) and starts with
    trial credits — resolved the same way as preview above, so org_admin/
    super_admin must pass the same centre_id they previewed with instead of
    silently orphaning students with no school at all (Student.centre_id is
    nullable, so this previously failed silently rather than erroring). A
    row's own `board`/`school` value always wins if present (e.g. a school
    with mixed-board sections, or a student whose real school differs from
    the uploading centre); otherwise a new student defaults to the school's
    own known board and name (see tenancy.default_board_for_centre/
    default_school_for_centre) instead of being left blank.
    """
    centre = _resolve_centre_for_write(db, teacher, body.centre_id)
    selected_features = {**DEFAULT_FEATURES, **body.features}
    centre_default_board = tenancy.default_board_for_centre(db, centre.id)
    centre_default_school = tenancy.default_school_for_centre(db, centre.id)

    # One query for every existing student this batch might touch, instead
    # of one query per row — a few-hundred-row upload previously meant a
    # few hundred round-trips to Postgres just to check for duplicates.
    batch_phones = [str(row.get("phone") or "").strip() for row in body.rows]
    existing_by_phone = {
        s.phone: s
        for s in db.query(Student).filter(Student.phone.in_(batch_phones), Student.centre_id == centre.id).all()
    }

    created, updated, skipped = [], [], []
    new_students = []
    for row in body.rows:
        name = str(row.get("name") or "").strip()
        phone = str(row.get("phone") or "").strip()
        if not name or not phone:
            skipped.append(row)
            continue

        # Scoped to the uploader's own school — matching by phone alone
        # would let one school's bulk upload silently overwrite another
        # school's student just by coincidentally listing the same number.
        existing = existing_by_phone.get(phone)
        if existing:
            existing.features = {**(existing.features or {}), **selected_features}
            if row.get("class"):
                existing.class_ = str(row["class"]).strip()
            if row.get("board"):
                existing.board = str(row["board"]).strip()
            if row.get("school"):
                existing.school = str(row["school"]).strip()
            updated.append(phone)
        else:
            new_student = Student(
                name=name, phone=phone,
                class_=str(row.get("class") or "").strip() or None,
                board=str(row.get("board") or "").strip() or centre_default_board,
                school=str(row.get("school") or "").strip() or centre_default_school,
                features=selected_features,
                centre_id=centre.id,
            )
            db.add(new_student)
            new_students.append(new_student)
            # So the SAME phone appearing again later in this batch updates
            # this just-created row instead of creating a second duplicate
            # profile for it.
            existing_by_phone[phone] = new_student
            created.append(phone)

    # Flush (not commit) so every new student gets its auto-generated id
    # without ending the transaction yet, saving N-1 round-trips on the
    # student inserts themselves. cost_tracker.add_trial_credits still
    # commits once per student internally (shared helper used everywhere
    # else in this codebase as a simple, immediately-committed credit
    # grant) — not worth changing that shared behavior just for this one
    # caller, so this part of the batch still isn't fully single-commit.
    db.flush()
    for new_student in new_students:
        cost_tracker.add_trial_credits(db, new_student.id)
    db.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


@router.patch("/admin/students/{student_id}")
def update_student(
    student_id: int, body: StudentUpdateRequest, db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    student = _get_scoped_student_or_404(db, teacher, student_id)

    if body.name is not None:
        student.name = body.name
    if body.phone is not None:
        # Never blanked out — Student.phone is how every WhatsApp message
        # gets matched back to this profile (see app.routers.whatsapp),
        # so an empty value here would silently orphan the student from
        # their own conversation history the next time they message.
        phone = body.phone.strip()
        if not phone:
            raise HTTPException(status_code=400, detail="phone cannot be empty")
        student.phone = phone
    if body.class_ is not None:
        student.class_ = body.class_
    if body.board is not None:
        student.board = body.board
    if body.school is not None:
        student.school = body.school
    if body.gender is not None:
        student.gender = body.gender or None
    if body.focus_topic is not None:
        student.focus_topic = body.focus_topic
    if body.features is not None:
        student.features = {**(student.features or {}), **body.features}
    if body.whatsapp_phone is not None:
        whatsapp_phone = body.whatsapp_phone.strip() or None
        if whatsapp_phone:
            # Two students routing WhatsApp messages to the same number
            # would be genuinely ambiguous (see _resolve_active_student) —
            # same one-number-one-owner reasoning as parent_phone below,
            # and also checked against every OTHER student's primary
            # `phone`, since that's the other column inbound messages can
            # match against.
            clash = (
                db.query(Student.id)
                .filter(
                    Student.id != student.id,
                    (Student.whatsapp_phone == whatsapp_phone) | (Student.phone == whatsapp_phone),
                )
                .first()
            )
            if clash:
                raise HTTPException(status_code=409, detail="That WhatsApp number is already linked to a different student")
        student.whatsapp_phone = whatsapp_phone
    if body.parent_phone is not None:
        # Parent.phone is globally unique — the same number can't be
        # linked to two different students' parent accounts.
        existing = db.query(Parent).filter(Parent.phone == body.parent_phone).first()
        if existing and existing.student_id != student.id:
            raise HTTPException(status_code=409, detail="That phone number is already linked to a different student")
        current = db.query(Parent).filter(Parent.student_id == student.id).first()
        if current:
            current.phone = body.parent_phone
            if body.parent_name is not None:
                current.name = body.parent_name
        else:
            db.add(Parent(student_id=student.id, phone=body.parent_phone, name=body.parent_name))
    db.commit()
    db.refresh(student)
    return _student_to_dict(db, student)


class SetStudentPasswordRequest(BaseModel):
    password: str


@router.post("/admin/students/{student_id}/set-password")
def set_student_password(
    student_id: int, body: SetStudentPasswordRequest, db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    """
    Gives a student a portal password — the only login option for a student
    with no WhatsApp access at all, since the normal path
    (POST /student-app/auth/request-otp) can only ever deliver a code over
    WhatsApp. A school admin/teacher sets this on the student's behalf
    (there's no "forgot password" self-serve flow for a student without
    WhatsApp either, for the same reason — an admin resets it the same way
    they set it originally). Once set, /student-app/auth/check-phone routes
    this phone to password login instead of OTP.
    """
    student = _get_scoped_student_or_404(db, teacher, student_id)
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    student.password_hash = hash_password(body.password)
    db.commit()
    return {"has_password": True}


@router.get("/admin/students/{student_id}/progress")
def student_progress(
    student_id: int, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    student = _get_scoped_student_or_404(db, teacher, student_id)
    stats = get_student_stats(db, student.id)
    activity = get_activity_stats(db, student.id)
    coverage = get_chapter_coverage(db, student)
    return {"stats": stats, "activity": activity, "coverage": coverage}


class DigestRequest(BaseModel):
    to_phone: str


@router.post("/admin/students/{student_id}/digest")
async def send_digest(
    student_id: int, body: DigestRequest, db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    student = _get_scoped_student_or_404(db, teacher, student_id)
    stats = get_student_stats(db, student.id, days=7)
    message = format_teacher_digest(
        student.name, stats,
        hints_given=student.hints_given_count or 0,
        direct_solutions=student.direct_solutions_count or 0,
    )
    result = await send_whatsapp_message(body.to_phone, f"📋 *Weekly Digest*\n\n{message}")
    if not result.get("sent"):
        raise HTTPException(status_code=502, detail=f"Failed to send: {result}")
    return {"sent": True}


class SendPaymentLinkRequest(BaseModel):
    to_phone: str | None = None  # defaults to the student's own WhatsApp number


@router.post("/admin/students/{student_id}/send-payment-link")
async def send_payment_link(
    student_id: int, body: SendPaymentLinkRequest, db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    student = _get_scoped_student_or_404(db, teacher, student_id)
    to_phone = body.to_phone or student.phone
    # student_id disambiguates a shared family phone with more than one
    # child enrolled on it — without it, a payment could silently land in
    # a sibling's wallet instead of this specific student's.
    link = f"{settings.portal_base_url}/pay?phone={student.phone}&student_id={student.id}"
    message = f"Top up *{student.name}*'s Qlass AI Tutor credits here: {link}"
    result = await send_whatsapp_message(to_phone, message)
    if not result.get("sent"):
        raise HTTPException(status_code=502, detail=f"Failed to send: {result}")
    return {"sent": True}


@router.get("/admin/students/{student_id}/credits")
def get_student_credits(
    student_id: int, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    student = _get_scoped_student_or_404(db, teacher, student_id)
    return {"balance": cost_tracker.get_balance(db, student.id)}


class AddCreditsRequest(BaseModel):
    amount: float
    note: str | None = None
    # Distinguishes a genuine refund (money actually returned/owed to this
    # school or student) from a goodwill/trial top-up or a manual ledger
    # correction — tagged on the CreditEvent's `service` field (reusing the
    # existing column rather than a new one) since a positive amount with
    # one of these tags is unambiguous against real usage deductions, which
    # are always negative amounts under a provider-service name.
    reason: str = "goodwill"  # "refund" | "goodwill" | "correction"


@router.post("/admin/students/{student_id}/credits/add")
def add_student_credits(
    student_id: int, body: AddCreditsRequest, db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    """
    Manually granting free credits (as opposed to a real Razorpay payment)
    is a Qlass-side goodwill/trial action, not something a school should be
    able to do to its own account unlimited times — restricted to Qlass's
    own super_admin role.
    """
    if teacher.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Qlass staff can manually grant credits")
    if body.reason not in ("refund", "goodwill", "correction"):
        raise HTTPException(status_code=400, detail="reason must be 'refund', 'goodwill', or 'correction'")
    student = _get_scoped_student_or_404(db, teacher, student_id)
    new_balance = cost_tracker.add_credits(db, student.id, body.amount, body.note, service=body.reason)
    audit_log.record(
        db, teacher.id, "grant_student_credits", "student", student.id,
        detail=f"amount=₹{body.amount:.2f} reason={body.reason} note={body.note or ''}",
    )
    return {"balance": new_balance}


TRIAL_UNLIMITED_SERVICE = "unlimited_plan_trial"
PAID_UNLIMITED_SERVICE = "unlimited_plan_activation"


def _activate_unlimited(
    db: Session, student: Student, duration_days: int, is_trial: bool,
    payment_reference: str | None, note: str | None, full_term_price: float, full_term_days: int,
    actor_teacher_id: int,
) -> None:
    """
    Shared by the single-student/single-teacher activation endpoints and
    the bulk school-level trial endpoint below. A trial grant records a $0
    CreditEvent tagged TRIAL_UNLIMITED_SERVICE with no external_ref — it
    must NOT satisfy cost_tracker.has_independent_payment, since nothing
    was actually paid. A real (non-trial) activation requires a
    payment_reference and records the real (possibly prorated) price with
    that reference as external_ref, so it both shows up in
    reconciliation/statements and correctly counts as an independent
    payment for churn-gating purposes.

    duration_days is bounded here (not just at each caller) since this is
    the one choke point every activation path — single-student, single-
    teacher, and bulk school-level — actually goes through. A negative
    value would compute a negative prorated `price` and backdate
    subscription_expires_at into the past. The upper bound is a generous
    sanity ceiling (a decade), not full_term_days itself — full_term_days
    is only the prorating denominator (365 for a student, 30 for a
    teacher) and a single bulk-activation call can legitimately mix both,
    so a tighter per-call cap here would reject a perfectly normal
    combined batch; the real "trial shouldn't outlast the paid term"
    business rule belongs at the caller that knows which kind of grant
    this is (see the bulk endpoint's own check).
    """
    if not 1 <= duration_days <= 3650:
        raise HTTPException(status_code=400, detail="duration_days must be between 1 and 3650")
    student.subscription_plan = "unlimited"
    student.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    if is_trial:
        cost_tracker.add_credits(
            db, student.id, 0.0, note=note or f"Unlimited plan TRIAL ({duration_days} days)",
            external_ref=None, service=TRIAL_UNLIMITED_SERVICE,
        )
    else:
        price = round(full_term_price * duration_days / full_term_days, 2)
        cost_tracker.add_credits(
            db, student.id, price, note=note or f"Unlimited plan activation ({duration_days} days)",
            external_ref=payment_reference, service=PAID_UNLIMITED_SERVICE,
        )
    audit_log.record(
        db, actor_teacher_id, "activate_subscription", "student", student.id,
        detail=f"duration_days={duration_days} is_trial={is_trial} payment_reference={payment_reference or ''}",
    )


class SetSubscriptionRequest(BaseModel):
    plan: str  # "credits" | "unlimited"
    duration_days: int | None = None  # required when plan == "unlimited"
    is_trial: bool = False  # a free trial grant — no payment_reference needed, but see _activate_unlimited
    # Required when plan == "unlimited" and not is_trial — a Razorpay
    # payment_id if collected through this app's own checkout, or a
    # free-text reference (e.g. "cash received by admin, receipt #42") if
    # the school collected it off-platform. Recorded as external_ref on the
    # payment CreditEvent, which is also what
    # cost_tracker.has_independent_payment checks — so this student is
    # correctly recognized as having paid directly even if their school
    # later gets marked "churned" (see school_billing.is_centre_churned).
    payment_reference: str | None = None
    note: str | None = None


@router.post("/admin/students/{student_id}/subscription")
def set_student_subscription(
    student_id: int, body: SetSubscriptionRequest, db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    """
    Manually activates the flat-fee unlimited plan (₹2499/yr, prorated if
    duration_days differs from the standard 365) or reverts a student back
    to the normal wallet. There's no recurring billing integration yet
    (Razorpay Subscriptions/UPI Autopay is a separate follow-up) — this is
    the manual activation Qlass staff use after collecting a payment,
    recording it as a real ledger entry (not just a bare flag flip) so it
    shows up in reconciliation/statements and satisfies the churn-gating
    "has this student paid independently" check. A trial grant (is_trial)
    skips the payment requirement entirely — see _activate_unlimited.

    GRANTING the plan (plan == "unlimited") is super_admin-only, since it
    involves payment tracking Qlass staff need to oversee. REVERTING it
    (plan == "credits") is open to the school's own teacher/admin too —
    since payment is taken upfront (or the trial is free) with no partial
    refund/mid-term churn, the only real reason to revert is a student
    leaving the school/organization, which the school's own staff are best
    placed to flag; they just no longer get the org-sponsored plan and
    would need to pay independently to keep using AI credits going forward
    (their existing wallet balance, if any, is untouched).
    """
    if body.plan == "unlimited" and teacher.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Qlass staff can grant the unlimited plan")
    if body.plan not in ("credits", "unlimited"):
        raise HTTPException(status_code=400, detail="plan must be 'credits' or 'unlimited'")
    if body.plan == "unlimited":
        if not body.duration_days:
            raise HTTPException(status_code=400, detail="duration_days is required to activate the unlimited plan")
        if not body.is_trial:
            if not body.payment_reference:
                raise HTTPException(status_code=400, detail="payment_reference is required for a paid activation")
            if cost_tracker.has_processed_external_ref(db, body.payment_reference):
                raise HTTPException(status_code=409, detail="This payment reference has already been used")

    student = _get_scoped_student_or_404(db, teacher, student_id)
    if body.plan == "unlimited":
        _activate_unlimited(
            db, student, body.duration_days, body.is_trial, body.payment_reference, body.note,
            cost_tracker.UNLIMITED_STUDENT_ANNUAL_PRICE, cost_tracker.UNLIMITED_STUDENT_ANNUAL_DAYS,
            teacher.id,
        )
    else:
        student.subscription_plan = "credits"
        student.subscription_expires_at = None
    db.commit()
    return {
        "subscription_plan": student.subscription_plan,
        "subscription_expires_at": student.subscription_expires_at,
    }


class BulkTrialActivationRequest(BaseModel):
    duration_days: int
    student_ids: list[int] = []
    teacher_ids: list[int] = []  # activates each teacher's own "My AI Tutor" profile
    note: str | None = None


@router.post("/admin/schools/{centre_id}/trial-subscriptions")
def activate_school_trial_subscriptions(
    centre_id: int, body: BulkTrialActivationRequest, db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    """
    Grants a free trial of the unlimited plan to a hand-picked set of
    students/teachers at one school in a single call — the sales-side
    counterpart to a school negotiating trial access for a subset of its
    people before committing to paying for everyone. Restricted to
    super_admin (Qlass staff) or an org_admin managing this school's
    organization (see _require_school_management_access) — always a trial
    (see _activate_unlimited) — no payment_reference, and tagged so it
    correctly does NOT count as an independent payment for churn-gating
    purposes.
    """
    if not body.student_ids and not body.teacher_ids:
        raise HTTPException(status_code=400, detail="Select at least one student or teacher")
    # Confirmed live this had NO upper bound, unlike the sibling pilot
    # endpoint just below (school_pilot.MAX_PILOT_STUDENTS/MAX_PILOT_DAYS) —
    # an org_admin (a lower-trust, customer-facing role, not Qlass staff)
    # could grant a school's entire roster the paid "unlimited" plan for
    # e.g. 100 years, for free, in one call. A "trial" longer than the real
    # paid plan's own term (UNLIMITED_STUDENT_ANNUAL_DAYS) makes no
    # business sense, so that's the ceiling here.
    if not 1 <= body.duration_days <= cost_tracker.UNLIMITED_STUDENT_ANNUAL_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Trial duration must be between 1 and {cost_tracker.UNLIMITED_STUDENT_ANNUAL_DAYS} days",
        )
    if len(body.student_ids) + len(body.teacher_ids) > MAX_PILOT_STUDENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many recipients in one activation (max {MAX_PILOT_STUDENTS}) — split into batches",
        )
    centre = db.query(Centre).filter(Centre.id == centre_id).first()
    if centre is None:
        raise HTTPException(status_code=404, detail="School not found")
    _require_school_management_access(teacher, centre)

    activated = []
    for student in (
        db.query(Student)
        .filter(Student.id.in_(body.student_ids), Student.centre_id == centre_id, Student.is_staff_profile.is_(False))
        .all()
    ):
        _activate_unlimited(
            db, student, body.duration_days, True, None, body.note,
            cost_tracker.UNLIMITED_STUDENT_ANNUAL_PRICE, cost_tracker.UNLIMITED_STUDENT_ANNUAL_DAYS,
            teacher.id,
        )
        activated.append(student.name)

    for target_teacher in db.query(Teacher).filter(Teacher.id.in_(body.teacher_ids), Teacher.centre_id == centre_id).all():
        my_tutor_student = _my_tutor_student(db, target_teacher)
        _activate_unlimited(
            db, my_tutor_student, body.duration_days, True, None, body.note,
            cost_tracker.UNLIMITED_TEACHER_MONTHLY_PRICE, cost_tracker.UNLIMITED_TEACHER_MONTHLY_DAYS,
            teacher.id,
        )
        activated.append(target_teacher.name)

    db.commit()
    return {"activated_count": len(activated), "activated": activated}


class LaunchSchoolPilotRequest(BaseModel):
    duration_days: int
    credits_per_student: float
    student_ids: list[int]
    teacher_tool_credits: float = 500.0
    note: str | None = None


@router.post("/admin/schools/{centre_id}/pilot")
def launch_school_pilot(
    centre_id: int, body: LaunchSchoolPilotRequest, db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    """
    Fund a bounded, no-charge school pilot with student wallet credits.
    Restricted to super_admin (Qlass staff) or an org_admin managing this
    school's organization — see _require_school_management_access.
    """
    if not body.student_ids:
        raise HTTPException(status_code=400, detail="Select at least one student for the pilot")
    centre = db.query(Centre).filter(Centre.id == centre_id).first()
    if centre is None:
        raise HTTPException(status_code=404, detail="School not found")
    _require_school_management_access(teacher, centre)
    try:
        students = launch_pilot(
            db, centre, body.student_ids, body.credits_per_student, body.duration_days,
            body.teacher_tool_credits, body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "pilot_status": centre.pilot_status,
        "pilot_expires_at": centre.pilot_expires_at,
        "credits_per_student": body.credits_per_student,
        "teacher_tool_credits": body.teacher_tool_credits,
        "enabled_features": [name for name, enabled in PILOT_STUDENT_FEATURES.items() if enabled],
        "granted_count": len(students),
        "granted": [student.name for student in students],
    }


@router.get("/admin/schools/{centre_id}/pilot/report")
def get_school_pilot_report(
    centre_id: int, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    """Principal/government decision report; scoped to the caller's school."""
    centre = _resolve_centre_for_read(db, teacher, centre_id)
    return pilot_outcome_report(db, centre)


def _teacher_to_dict(t: Teacher) -> dict:
    return {"id": t.id, "name": t.name, "phone": t.phone, "role": t.role, "centre_id": t.centre_id, "photo_url": t.photo_url}


@router.get("/admin/teachers")
def list_teachers(
    limit: int = QueryParam(default=100, ge=1, le=500),
    offset: int = QueryParam(default=0, ge=0),
    db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    if teacher.role not in ("admin", "org_admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can view teacher accounts")
    query = db.query(Teacher)
    if teacher.role == "org_admin":
        query = query.filter(Teacher.centre_id.in_(_org_centre_ids(db, teacher.organization_id)))
    elif teacher.role != "super_admin":
        query = query.filter(Teacher.centre_id == teacher.centre_id)
    return [_teacher_to_dict(t) for t in query.order_by(Teacher.id).offset(offset).limit(limit).all()]


class TeacherCreateRequest(BaseModel):
    name: str
    phone: str
    password: str
    role: str = "teacher"
    # Only used (and required) when the creator is super_admin, who has no
    # centre of their own to default to — a school admin's new hires are
    # always placed in the admin's own school instead (see below).
    centre_id: int | None = None


@router.post("/admin/teachers")
async def create_teacher(
    body: TeacherCreateRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    """
    Lets a school's own admin add teacher accounts for their school without
    needing CLI/database access — necessary now that schools can self-
    register (see /auth/register-school), since a non-technical school
    admin has no other way to provision their staff's logins. An org_admin
    (e.g. a government programme spanning many schools) may add accounts
    for any school within their own organization — covers roles like a
    headmaster/vice-principal (both just "admin" at their own school).
    """
    if teacher.role not in ("admin", "org_admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can add teacher accounts")
    if body.role not in ("teacher", "admin"):
        raise HTTPException(status_code=400, detail="Role must be 'teacher' or 'admin'")
    _require_valid_password(body.password)
    phone = normalize_phone(body.phone)
    if db.query(Teacher).filter(Teacher.phone == phone).first():
        raise HTTPException(status_code=409, detail="An account with this phone number already exists")

    if teacher.role == "admin":
        centre_id = teacher.centre_id
    else:
        if not body.centre_id:
            raise HTTPException(status_code=400, detail="centre_id is required")
        centre_id = body.centre_id
        if teacher.role == "org_admin":
            centre = db.query(Centre).filter(Centre.id == centre_id).first()
            if centre is None or centre.organization_id != teacher.organization_id:
                raise HTTPException(status_code=403, detail="That school is not part of your organization")

    new_teacher = Teacher(
        name=body.name, phone=phone, password_hash=hash_password(body.password),
        role=body.role, centre_id=centre_id,
    )
    db.add(new_teacher)
    db.commit()
    db.refresh(new_teacher)
    # Previously a completely silent DB row — a newly "invited" teacher had
    # no way of knowing an account existed unless someone told them
    # out-of-band. Best-effort: shouldn't fail account creation if the send
    # itself fails.
    try:
        await send_whatsapp_message(
            phone,
            f"Welcome to Qlass, {body.name}! 🎉 An account has been set up for you on the Qlass "
            f"Partner Console.\n\nLog in with:\nPhone: {phone}\nPassword: {body.password}\n\n"
            f"You can change your password any time from the login page's \"Forgot password?\" link.",
        )
    except Exception:
        pass
    return _teacher_to_dict(new_teacher)


@router.post("/admin/teachers/bulk-upload/preview")
async def preview_teacher_bulk_upload(
    file: UploadFile = File(...), db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    """
    Parses a CSV, or OCRs/extracts a staff-list photo, PDF, or Word file,
    into row previews for the admin to review — and correct — before
    anything is created. Expected fields per row: name, phone, role
    (teacher/admin — a headmaster/principal/vice-principal is just
    "admin" for their own school), centre_id (required for org_admin/
    super_admin, since a batch can span multiple schools; ignored for a
    plain school admin). A photo/scan can never contain real passwords, so
    those are always generated fresh at confirm time, not extracted here.
    """
    if teacher.role not in ("admin", "org_admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can bulk-upload teacher accounts")
    rows = await _rows_from_roster_upload(file, extract_teacher_rows, db, teacher.centre_id)
    return {"rows": rows}


class ConfirmTeacherBulkUploadRequest(BaseModel):
    rows: list[dict] = Field(max_length=MAX_ROSTER_UPLOAD_ROWS)


@router.post("/admin/teachers/bulk-upload/confirm")
def confirm_teacher_bulk_upload(
    body: ConfirmTeacherBulkUploadRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    """
    Actually creates teacher accounts from previously-previewed (and
    possibly hand-corrected) rows — see .../bulk-upload/preview. A row's
    own `password` wins if the admin filled one in during review;
    otherwise a random temporary password is generated and returned so it
    can be shared with that teacher (there is no other channel to deliver
    it — this product has no teacher-facing signup email/SMS flow).
    """
    if teacher.role not in ("admin", "org_admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can bulk-upload teacher accounts")

    org_centre_ids = (
        {row.id for row in _org_centre_ids(db, teacher.organization_id).all()}
        if teacher.role == "org_admin" else None
    )

    # One query for every phone this batch might touch, instead of one
    # query per row.
    batch_phones = [str(row.get("phone") or "").strip() for row in body.rows]
    existing_phones = {
        t.phone for t in db.query(Teacher.phone).filter(Teacher.phone.in_(batch_phones)).all()
    }

    created, skipped, generated_passwords = [], [], {}
    for row in body.rows:
        name = str(row.get("name") or "").strip()
        phone = str(row.get("phone") or "").strip()
        role = str(row.get("role") or "teacher").strip().lower()
        if not name or not phone or role not in ("teacher", "admin"):
            skipped.append(row)
            continue
        if phone in existing_phones:
            # Also catches the SAME phone appearing twice within this batch
            # — the phone column is UNIQUE, so without this check a
            # duplicate row would only surface as an IntegrityError on the
            # final commit, rolling back every other row in the batch too.
            skipped.append(row)
            continue
        existing_phones.add(phone)

        if teacher.role == "admin":
            centre_id = teacher.centre_id
        else:
            row_centre_id = str(row.get("centre_id") or "").strip()
            if not row_centre_id or not row_centre_id.isdigit():
                skipped.append(row)
                continue
            centre_id = int(row_centre_id)
            if teacher.role == "org_admin" and centre_id not in org_centre_ids:
                skipped.append(row)
                continue

        password = str(row.get("password") or "").strip()
        if not password:
            password = secrets.token_urlsafe(6)
            generated_passwords[phone] = password
        elif len(password) < MIN_PASSWORD_LENGTH:
            skipped.append(row)
            continue

        db.add(Teacher(name=name, phone=phone, password_hash=hash_password(password), role=role, centre_id=centre_id))
        created.append(phone)
    db.commit()
    return {
        "created_count": len(created), "created": created, "skipped_count": len(skipped),
        "generated_passwords": generated_passwords,
    }


def _resolve_centre_for_read(db: Session, teacher: Teacher, centre_id: int | None) -> Centre:
    """
    Any signed-in teacher/admin can VIEW their own school's profile and
    credit balance (needed for the workbook generator to show remaining
    balance) — only WRITE actions (logo, manual credit grants) are
    restricted further, see _resolve_centre_for_write below. org_admin may
    view any school within their own organization (see the Organization
    model), not just one fixed centre.
    """
    target_id = teacher.centre_id if teacher.role in ("teacher", "admin") else centre_id
    if not target_id:
        raise HTTPException(status_code=400, detail="centre_id is required")
    centre = db.query(Centre).filter(Centre.id == target_id).first()
    if not centre:
        raise HTTPException(status_code=404, detail="School not found")
    if teacher.role == "org_admin" and centre.organization_id != teacher.organization_id:
        raise HTTPException(status_code=403, detail="This school is not part of your organization")
    return centre


def _resolve_centre_for_write(db: Session, teacher: Teacher, centre_id: int | None) -> Centre:
    """
    A school's own admin always edits their own school; org_admin may edit
    any school within their own organization; only super_admin (Qlass
    staff, who has no centre of their own) may target a different school
    outside any organization, and must say which one via centre_id —
    mirrors the same pattern used for admin-vs-super_admin teacher
    creation above.
    """
    if teacher.role not in ("admin", "org_admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can manage the school profile")
    return _resolve_centre_for_read(db, teacher, centre_id)


@router.get("/admin/school")
def get_school(
    centre_id: int | None = None, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    centre = _resolve_centre_for_read(db, teacher, centre_id)
    return {
        "id": centre.id, "name": centre.name, "city": centre.city, "logo_url": centre.logo_url,
        "board": centre.board, "credit_balance": school_billing.get_balance(db, centre.id),
        "auto_approve_students": centre.auto_approve_students if centre.auto_approve_students is not None else True,
    }


class UpdateSchoolProfileRequest(BaseModel):
    centre_id: int | None = None
    name: str | None = None
    city: str | None = None
    # The school's own board (e.g. "CBSE", "BSEB") — new students under this
    # school default to this instead of being asked individually (see
    # tenancy.default_board_for_centre).
    board: str | None = None
    # See Centre.auto_approve_students — a self-registered student either
    # becomes a full roster member immediately, or starts "pending" until
    # a teacher confirms them.
    auto_approve_students: bool | None = None


@router.patch("/admin/school")
def update_school_profile(
    body: UpdateSchoolProfileRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    centre = _resolve_centre_for_write(db, teacher, body.centre_id)
    if body.name is not None:
        centre.name = body.name
    if body.city is not None:
        centre.city = body.city
    if body.board is not None:
        centre.board = body.board
    if body.auto_approve_students is not None:
        centre.auto_approve_students = body.auto_approve_students
    db.commit()
    return {
        "id": centre.id, "name": centre.name, "city": centre.city, "board": centre.board,
        "auto_approve_students": centre.auto_approve_students,
    }


class AddSchoolCreditsRequest(BaseModel):
    amount: float
    note: str | None = None
    centre_id: int | None = None
    reason: str = "goodwill"  # "refund" | "goodwill" | "correction" — see AddCreditsRequest


@router.post("/admin/school/credits/add")
def add_school_credits(
    body: AddSchoolCreditsRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    """
    Manually granting school-level credit (workbook/presentation tools), as
    opposed to the school actually paying — same Qlass-goodwill-only
    restriction as the per-student equivalent (see /admin/students/{id}/credits/add).
    """
    if teacher.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Qlass staff can manually grant school credits")
    if body.reason not in ("refund", "goodwill", "correction"):
        raise HTTPException(status_code=400, detail="reason must be 'refund', 'goodwill', or 'correction'")
    centre = _resolve_centre_for_write(db, teacher, body.centre_id)
    new_balance = school_billing.add_credits(db, centre.id, body.amount, body.note, service=body.reason)
    audit_log.record(
        db, teacher.id, "grant_school_credits", "centre", centre.id,
        detail=f"amount=₹{body.amount:.2f} reason={body.reason} note={body.note or ''}",
    )
    return {"balance": new_balance}


@router.post("/admin/school/logo")
async def upload_school_logo(
    file: UploadFile = File(...),
    centre_id: int | None = Form(None),
    db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    centre = _resolve_centre_for_write(db, teacher, centre_id)
    centre.logo_url = await save_image_upload(file, "logos")
    db.commit()
    return {"logo_url": centre.logo_url}


@router.post("/admin/students/{student_id}/photo")
async def upload_student_photo(
    student_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    student = _get_scoped_student_or_404(db, teacher, student_id)
    student.photo_url = await save_image_upload(file, "student-photos")
    db.commit()
    return {"photo_url": student.photo_url}


def _get_scoped_teacher_or_404(db: Session, teacher: Teacher, target_teacher_id: int) -> Teacher:
    query = db.query(Teacher).filter(Teacher.id == target_teacher_id)
    if teacher.role != "super_admin":
        query = query.filter(Teacher.centre_id == teacher.centre_id)
    target = query.first()
    if not target:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return target


@router.post("/admin/teachers/{teacher_id}/photo")
async def upload_teacher_photo(
    teacher_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    # A teacher may always upload their own photo; uploading someone else's
    # additionally requires admin/super_admin (same scoping as every other
    # teacher-management action).
    if teacher_id == teacher.id:
        target = teacher
    else:
        if teacher.role not in ("admin", "super_admin"):
            raise HTTPException(status_code=403, detail="Only admins can update another teacher's photo")
        target = _get_scoped_teacher_or_404(db, teacher, teacher_id)
    target.photo_url = await save_image_upload(file, "teacher-photos")
    db.commit()
    return {"photo_url": target.photo_url}


def _resolve_chapters_topic(db: Session, chapter_ids: list[int], extra_focus: str | None) -> str:
    """
    Combines one or more selected chapters into a single topic string for
    the question/slide generator, preserving the order the teacher picked
    them in (not DB order) — e.g. chapter_ids=[7, 3] on Real Numbers (3)
    and Coordinate Geometry (7) produces "Coordinate Geometry, Real
    Numbers". extra_focus (optional) narrows within those chapters (e.g.
    "just HCF and LCM") rather than covering each in full.
    """
    chapters = db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).all()
    chapters_by_id = {c.id: c for c in chapters}
    missing = [cid for cid in chapter_ids if cid not in chapters_by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"Chapter(s) not found: {missing}")
    names = ", ".join(chapters_by_id[cid].name for cid in chapter_ids)
    return f"{names}: {extra_focus.strip()}" if extra_focus and extra_focus.strip() else names


class WorkbookGenerateRequest(BaseModel):
    topic: str | None = None
    chapter_ids: list[int] = []
    class_: str | None = None
    board: str | None = None
    num_questions: int = 10
    include_answer_key: bool = True


@router.post("/admin/workbook/generate")
async def generate_workbook(
    body: WorkbookGenerateRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    """
    Teacher-facing practice-set PDF generator — billed to the school's own
    credit ledger (school_billing), not any student's wallet, since this is
    a teacher tool rather than tutoring usage. Chapter selection (from the
    seeded curriculum for the given class/board) is the primary path, same
    as quiz assignment and presentations — a bare free-text topic with no
    class/chapter context produces generic, unpitched worksheets, so
    chapter_ids is how a real syllabus-aligned worksheet gets its depth
    and scope; topic alone remains for a genuinely custom topic outside
    the seeded curriculum.
    """
    if teacher.role == "super_admin":
        raise HTTPException(status_code=400, detail="Sign in as a school's own teacher/admin to generate a workbook")
    if not school_billing.has_credits(db, teacher.centre_id):
        raise HTTPException(status_code=402, detail="Your school is out of credits for workbook generation")

    if body.chapter_ids:
        topic = _resolve_chapters_topic(db, body.chapter_ids, body.topic)
    else:
        topic = body.topic
    if not topic:
        raise HTTPException(status_code=400, detail="Either topic or chapter_ids is required")

    centre = db.query(Centre).filter(Centre.id == teacher.centre_id).first()

    questions, llm_result = await generate_workbook_questions(topic, body.class_, body.num_questions, board=body.board)
    school_billing.record_claude_usage(
        db, teacher.centre_id, "workbook_pdf", llm_result.input_tokens, llm_result.output_tokens
    )
    if not questions:
        raise HTTPException(status_code=502, detail="Couldn't generate questions for that topic — try again")

    pdf_bytes = render_workbook_pdf(
        topic=topic, class_=body.class_, school_name=centre.name, school_logo_url=centre.logo_url,
        questions=questions, include_answer_key=body.include_answer_key,
    )
    filename = f"{topic.replace(' ', '_').replace(',', '')[:60]}_worksheet.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/admin/curriculum/chapters")
def list_curriculum_chapters(
    class_: str, board: str = "CBSE", db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    """
    Chapters for the seeded curriculum (see scripts/seed_ncert_curriculum.py
    for CBSE/NCERT, scripts/seed_bseb_curriculum.py for BSEB), grouped by
    subject — lets a teacher assign a quiz tied to an actual chapter
    instead of a free-text topic prone to typos/ambiguity. `board` defaults
    to CBSE (the original, larger seeded curriculum) for backward
    compatibility with existing callers that don't pass it yet. Curriculum
    data isn't centre-scoped (it's the shared syllabus for that board), so
    any authenticated teacher/admin can browse it.
    """
    rows = (
        db.query(Chapter, Subject)
        .join(Subject, Chapter.subject_id == Subject.id)
        .filter(Subject.class_ == class_, Subject.board == board)
        .order_by(Subject.name, Chapter.chapter_no)
        .all()
    )
    return [
        {"id": chapter.id, "name": chapter.name, "chapter_no": chapter.chapter_no, "subject": subject.name}
        for chapter, subject in rows
    ]


class AssignQuizRequest(BaseModel):
    topic: str | None = None
    chapter_ids: list[int] = []  # takes priority over topic when both are given; may span multiple chapters
    class_: str | None = None
    board: str | None = None
    phone_numbers: list[str] | None = None  # bypass class_/board filters, target these students directly


@router.post("/admin/quizzes/assign")
async def assign_quiz(
    body: AssignQuizRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    """
    Teacher-authored quiz, broadcast to a whole class at once over WhatsApp.
    Generates ONE question set — billed once to the school's own credit
    ledger, same pattern as generate_workbook above — then clones it into an
    independent Quiz+Question set per targeted student, so each student
    progresses through their copy via the exact same per-student
    active_quiz_id turn-by-turn flow as an ad-hoc "quiz me on X" request
    (see whatsapp.py's active_quiz_id branch) — no changes needed there,
    grading is already automatic.
    """
    if teacher.role == "super_admin":
        raise HTTPException(status_code=400, detail="Sign in as a school's own teacher/admin to assign a quiz")
    if not school_billing.has_credits(db, teacher.centre_id):
        raise HTTPException(status_code=402, detail="Your school is out of credits for quiz generation")

    if body.chapter_ids:
        topic = _resolve_chapters_topic(db, body.chapter_ids, body.topic)
    else:
        topic = body.topic
    if not topic:
        raise HTTPException(status_code=400, detail="Either topic or chapter_ids is required")

    # A single generated question set is sent to every targeted student
    # verbatim — without a class, it's tuned to no grade level at all, and
    # without this check it would previously go out to literally every
    # student at the school regardless of age (a Class 3 and a Class 12
    # student getting the identical quiz). A class is required so the
    # content is pedagogically appropriate for whoever actually receives it.
    if not body.class_ and not body.phone_numbers:
        raise HTTPException(status_code=400, detail="A class is required to assign a quiz")

    if body.phone_numbers:
        query = db.query(Student).filter(Student.phone.in_(body.phone_numbers), Student.centre_id == teacher.centre_id)
    else:
        query = db.query(Student).filter(Student.centre_id == teacher.centre_id)
        if body.class_:
            query = query.filter(Student.class_ == body.class_)
        if body.board:
            query = query.filter(Student.board == body.board)
    targets = query.filter(Student.is_staff_profile.is_(False), Student.is_deleted.is_(False)).all()
    if not targets:
        raise HTTPException(status_code=400, detail="No matching students found")

    # Explicit phone_numbers targeting bypasses the class_/board filters
    # above, so it's not guaranteed every target shares one class the way
    # the filtered path is — check directly, since the same "one quiz for
    # everyone" content-mismatch risk applies just as much here.
    target_classes = {s.class_ for s in targets if s.class_}
    if len(target_classes) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"Selected students span multiple classes ({', '.join(sorted(target_classes))}) — "
            "assign separately per class so the quiz is pitched at the right level for each.",
        )
    effective_class = body.class_ or (next(iter(target_classes)) if target_classes else None)
    target_boards = {s.board for s in targets if s.board}
    effective_board = body.board or (next(iter(target_boards)) if len(target_boards) == 1 else None)

    questions_data, gen_result = await generate_quiz_questions(topic, effective_class, board=effective_board)
    school_billing.record_claude_usage(
        db, teacher.centre_id, "quiz_assignment", gen_result.input_tokens, gen_result.output_tokens
    )
    if not questions_data:
        raise HTTPException(status_code=502, detail="Couldn't generate questions for that topic — try again")

    # One commit for the whole batch instead of three per student (quiz,
    # questions, active_quiz_id) — a class-sized assignment previously
    # meant up to 3x the number of students in round-trips to Postgres.
    # db.flush() (not commit) gets each quiz's auto-generated id without
    # ending the transaction, so all of it lands atomically at the end.
    assigned, skipped = [], []
    pending_notifications = []
    for student in targets:
        if student.active_quiz_id:
            skipped.append(student.name)  # already mid-quiz — don't interrupt it with a new one
            continue
        quiz = Quiz(
            # Quiz.chapter_id is a single FK and can't represent a quiz
            # spanning multiple chapters — informational best-effort only
            # (the first chapter picked), not something anything else reads.
            student_id=student.id, created_by_teacher_id=teacher.id, title=topic,
            chapter_id=body.chapter_ids[0] if body.chapter_ids else None,
        )
        db.add(quiz)
        db.flush()
        for q in questions_data:
            db.add(Question(
                quiz_id=quiz.id, question_type=q.get("question_type", "short_answer"),
                question_text=q["question"], correct_answer=q["answer"],
            ))
        student.active_quiz_id = quiz.id
        pending_notifications.append(student.phone)
        assigned.append(student.name)
    db.commit()

    intro = (
        f"📝 Your teacher assigned a {len(questions_data)}-question quiz on *{topic}*!\n\n"
        f"Question 1/{len(questions_data)}: {questions_data[0]['question']}"
    )
    for phone in pending_notifications:
        await send_whatsapp_message(phone, intro)

    return {"assigned_count": len(assigned), "assigned": assigned, "skipped_already_in_quiz": skipped}


class SchoolCreateOrderRequest(BaseModel):
    amount: float


@router.post("/admin/school/pay/create-order")
def create_school_order(
    body: SchoolCreateOrderRequest, teacher: Teacher = Depends(get_current_teacher)
):
    """
    Lets any teacher/admin at a school pay out of pocket to top up their
    OWN school's shared credit ledger (workbook/presentation tools) as an
    add-on beyond whatever allowance the school itself provides — no phone
    lookup needed since the teacher is already authenticated, unlike the
    public student /pay flow.
    """
    if teacher.role == "super_admin":
        raise HTTPException(status_code=400, detail="Sign in as a school's own teacher/admin to top up credits")
    if _razorpay_client is None:
        raise HTTPException(status_code=503, detail="Payments aren't configured yet — contact Qlass support")
    if body.amount < MIN_TOPUP_AMOUNT:
        raise HTTPException(status_code=400, detail=f"Minimum top-up is ₹{MIN_TOPUP_AMOUNT:.0f}")

    order = _razorpay_client.order.create({
        "amount": int(round(body.amount * 100)),
        "currency": "INR",
        "notes": {"centre_id": str(teacher.centre_id), "teacher_id": str(teacher.id)},
    })
    return {"order_id": order["id"], "key_id": settings.razorpay_key_id, "amount": order["amount"], "currency": "INR"}


class SchoolVerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/admin/school/pay/verify")
def verify_school_payment(
    body: SchoolVerifyPaymentRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    if _razorpay_client is None:
        raise HTTPException(status_code=503, detail="Payments aren't configured yet — contact Qlass support")

    try:
        _razorpay_client.utility.verify_payment_signature({
            "razorpay_order_id": body.razorpay_order_id,
            "razorpay_payment_id": body.razorpay_payment_id,
            "razorpay_signature": body.razorpay_signature,
        })
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Payment could not be verified")

    # Re-fetch the order from Razorpay itself rather than trusting any
    # client-supplied amount, and bind it to the authenticated teacher's
    # school so another school's signed payment cannot be replayed here.
    order = _razorpay_client.order.fetch(body.razorpay_order_id)
    payment = _razorpay_client.payment.fetch(body.razorpay_payment_id)
    try:
        razorpay_client.require_paid_order(
            order, payment, {"centre_id": str(teacher.centre_id), "teacher_id": str(teacher.id)}
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Payment does not match this school") from exc
    if school_billing.has_processed_external_ref(db, body.razorpay_payment_id):
        return {"credited": 0.0, "balance": school_billing.get_balance(db, teacher.centre_id)}
    amount_inr = order["amount"] / 100
    new_balance = school_billing.add_credits(
        db, teacher.centre_id, amount_inr, note=f"Razorpay payment by {teacher.name} ({body.razorpay_payment_id})",
        external_ref=body.razorpay_payment_id,
    )
    return {"credited": amount_inr, "balance": new_balance}


class PresentationGenerateRequest(BaseModel):
    topic: str | None = None
    chapter_ids: list[int] = []  # may span multiple chapters
    class_: str | None = None
    board: str | None = None
    num_cards: int = 8


@router.post("/admin/presentation/generate")
async def generate_presentation(
    body: PresentationGenerateRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    """
    Same teacher-tool billing model as the workbook generator — see that
    endpoint's docstring. Requires a real GAMMA_API_KEY in .env (Gamma's
    Generate API is a paid beta); until then this 503s cleanly, matching
    the Razorpay "not configured yet" pattern.

    class_/board (and chapter_id, which resolves to a subject + chapter
    name) give Gamma real curriculum context instead of a bare topic string
    — without this, a request like "Real Numbers" produces a generic deck
    with no sense of grade level or board-specific depth/terminology,
    exactly like the same gap AssignQuiz's chapter picker exists to close.
    """
    if teacher.role == "super_admin":
        raise HTTPException(status_code=400, detail="Sign in as a school's own teacher/admin to generate a presentation")
    if not settings.gamma_api_key:
        raise HTTPException(status_code=503, detail="Presentation generation isn't configured yet — contact Qlass support")
    if not school_billing.has_credits(db, teacher.centre_id):
        raise HTTPException(status_code=402, detail="Your school is out of credits for presentation generation")

    if body.chapter_ids:
        topic = _resolve_chapters_topic(db, body.chapter_ids, body.topic)
    else:
        topic = body.topic
    if not topic:
        raise HTTPException(status_code=400, detail="topic or chapter_ids is required")

    context_parts = [topic]
    if body.class_:
        context_parts.append(f"for Class {body.class_}")
    if body.board:
        context_parts.append(f"({body.board} curriculum)")
    input_text = " ".join(context_parts)

    try:
        generation_id = await create_presentation_generation(input_text, body.num_cards)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Presentation service is temporarily unavailable — please try again shortly")
    return {"generation_id": generation_id}


@router.get("/admin/presentation/status/{generation_id}")
async def presentation_status(
    generation_id: str, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    if not settings.gamma_api_key:
        raise HTTPException(status_code=503, detail="Presentation generation isn't configured yet — contact Qlass support")
    try:
        result = await get_generation_status(generation_id)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Presentation service is temporarily unavailable — please try again shortly")

    # Billed here (from Gamma's own reported credit cost) rather than a
    # flat guess at creation time — real cost varies a lot by slide count
    # and image options. Guarded against double-billing across the
    # frontend's repeated polls by has_billed_gamma_generation.
    credits_deducted = result.get("credits_deducted")
    if result.get("status") == "completed" and credits_deducted is not None:
        if not school_billing.has_billed_gamma_generation(db, generation_id):
            school_billing.record_gamma_usage(db, teacher.centre_id, generation_id, credits_deducted)

    return {"status": result.get("status"), "url": result.get("url")}


def _my_tutor_student(db: Session, teacher: Teacher) -> Student:
    return get_or_create_linked_student(db, teacher.phone, teacher.name, teacher.centre_id)


def _my_tutor_credits_exhausted_detail(student: Student) -> str:
    """
    An unlimited-plan teacher reaching this point has burned through their
    day/week/month flat-fee allotment (see
    cost_tracker.is_unlimited_over_period_cap) AND has no wallet balance —
    "out of AI credits" would be confusing for someone already paying a
    flat fee, so this is worded as topping up "usage credits" to keep
    going until their plan resets, not paying for the plan itself again
    (same distinction made on WhatsApp and the student web app).
    """
    if cost_tracker.is_unlimited_active(student):
        return (
            "You've used up your plan's included AI usage for this period — top up usage credits to keep "
            "going until it resets, from the My AI Tutor page"
        )
    return f"You're out of AI credits for your personal tutor account — top up from the My AI Tutor page, or call Qlass support at {QLASS_SUPPORT_PHONE}"


async def _my_tutor_reply(db: Session, student: Student, message_text: str) -> dict:
    """
    Mirrors app.routers.student_app._reply_to's per-student lock — these
    four /admin/my-tutor/chat/send* endpoints run the exact same
    has_credits-check-through-deduction sequence (via process_web_message)
    but, until this fix, had no lock at all, unlike WhatsApp and the
    student web app. Two concurrent requests from the same teacher's own
    tutor session could both pass has_credits() before either deduction
    committed, taking the balance further negative than a single overdraft
    should allow, and could double-pay a habit/referral milestone bonus the
    same way (see evaluate_habit_milestones/evaluate_referral_milestones,
    both read-check-write against the student row this lock now serializes).
    """
    async with student_lock(student.phone):
        if not cost_tracker.has_credits(db, student.id):
            raise HTTPException(status_code=402, detail=_my_tutor_credits_exhausted_detail(student))
        reply = await process_web_message(db, student, message_text)
        return {"reply": reply, "credit_balance": cost_tracker.get_balance(db, student.id)}


@router.get("/admin/my-tutor")
def get_my_tutor_profile(db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)):
    """
    Lets a teacher/admin use the AI tutor personally, on their own school
    account — lazily creates a student-like profile tied to their own
    phone/school the first time they open this, with its own credit
    wallet, kept entirely separate from any real student's.
    """
    student = _my_tutor_student(db, teacher)
    return {
        "id": student.id, "credit_balance": cost_tracker.get_balance(db, student.id),
        "referral_code": student.referral_code,
        "subscription_plan": student.subscription_plan,
        "subscription_expires_at": student.subscription_expires_at,
        "auto_renewing": student.razorpay_subscription_id is not None,
        "tutor_level": student.tutor_level,
    }


class SetMyTutorLevelRequest(BaseModel):
    level: int


@router.post("/admin/my-tutor/tutor-level")
def set_my_tutor_level(
    body: SetMyTutorLevelRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    """Teacher/admin equivalent of POST /student-app/tutor-level — same
    direct switch, same pending_level_offer clear, just against the
    teacher's own linked My AI Tutor profile instead of a real student."""
    if body.level not in TUTOR_LEVEL_MODELS:
        raise HTTPException(status_code=400, detail="level must be between 1 and 4")
    student = _my_tutor_student(db, teacher)
    student.tutor_level = body.level
    student.pending_level_offer = None
    db.commit()
    return {"id": student.id, "tutor_level": student.tutor_level}


@router.post("/admin/my-tutor/subscription/create")
def create_my_tutor_subscription(db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)):
    """
    Self-serve recurring auto-renewal for a teacher's own ₹3500/mo personal
    tutor plan — the teacher pays for themselves via Razorpay's
    Subscriptions API, rather than needing a super_admin to manually
    activate/collect payment for it (see
    /admin/teachers/{id}/my-tutor-subscription/activate, still available
    for Qlass staff to activate on a teacher's behalf).
    """
    if _razorpay_client is None or not settings.razorpay_teacher_plan_id:
        raise HTTPException(status_code=503, detail="Subscriptions aren't configured yet — contact Qlass support")
    student = _my_tutor_student(db, teacher)
    if cost_tracker.is_unlimited_active(student):
        raise HTTPException(status_code=400, detail="You're already on the unlimited plan")
    subscription = razorpay_client.create_subscription(
        settings.razorpay_teacher_plan_id, razorpay_client.TEACHER_SUBSCRIPTION_TOTAL_CYCLES,
        notes={"student_id": str(student.id), "teacher_id": str(teacher.id), "kind": "teacher_monthly"},
    )
    return {"subscription_id": subscription["id"], "key_id": settings.razorpay_key_id}


class VerifyMyTutorSubscriptionRequest(BaseModel):
    razorpay_subscription_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/admin/my-tutor/subscription/verify")
def verify_my_tutor_subscription(
    body: VerifyMyTutorSubscriptionRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    """Confirms the FIRST payment on a new subscription mandate — see create_my_tutor_subscription."""
    if _razorpay_client is None:
        raise HTTPException(status_code=503, detail="Subscriptions aren't configured yet — contact Qlass support")
    try:
        _razorpay_client.utility.verify_subscription_payment_signature({
            "razorpay_subscription_id": body.razorpay_subscription_id,
            "razorpay_payment_id": body.razorpay_payment_id,
            "razorpay_signature": body.razorpay_signature,
        })
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Payment could not be verified")

    student = _my_tutor_student(db, teacher)
    subscription = razorpay_client.fetch_subscription(body.razorpay_subscription_id)
    payment = _razorpay_client.payment.fetch(body.razorpay_payment_id)
    try:
        razorpay_client.require_active_subscription(
            subscription,
            payment,
            {"student_id": str(student.id), "teacher_id": str(teacher.id), "kind": "teacher_monthly"},
            settings.razorpay_teacher_plan_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Subscription does not match this teacher") from exc

    if cost_tracker.has_processed_external_ref(db, body.razorpay_payment_id):
        return {"subscription_plan": student.subscription_plan, "subscription_expires_at": student.subscription_expires_at}

    student.subscription_plan = "unlimited"
    student.subscription_expires_at = datetime.now(timezone.utc) + timedelta(
        days=cost_tracker.UNLIMITED_TEACHER_MONTHLY_DAYS
    )
    student.razorpay_subscription_id = body.razorpay_subscription_id
    cost_tracker.add_credits(
        db, student.id, cost_tracker.UNLIMITED_TEACHER_MONTHLY_PRICE,
        note="Razorpay subscription — first payment", external_ref=body.razorpay_payment_id,
        service="unlimited_plan_recurring",
    )
    db.commit()
    return {"subscription_plan": student.subscription_plan, "subscription_expires_at": student.subscription_expires_at}


@router.post("/admin/my-tutor/subscription/cancel")
def cancel_my_tutor_subscription(db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)):
    """Self-serve cancellation — see payments.cancel_student_subscription for the same behavior/reasoning."""
    if _razorpay_client is None:
        raise HTTPException(status_code=503, detail="Subscriptions aren't configured yet — contact Qlass support")
    student = _my_tutor_student(db, teacher)
    if not student.razorpay_subscription_id:
        raise HTTPException(status_code=400, detail="No active auto-renewing subscription found")
    razorpay_client.cancel_subscription(student.razorpay_subscription_id)
    student.razorpay_subscription_id = None
    db.commit()
    return {"cancelled": True, "access_until": student.subscription_expires_at}


@router.get("/admin/my-tutor/history")
def get_my_tutor_history(db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)):
    student = _my_tutor_student(db, teacher)
    rows = (
        db.query(ChatHistory)
        .filter(ChatHistory.student_id == student.id)
        .order_by(ChatHistory.created_at.desc())
        .limit(MY_TUTOR_CHAT_HISTORY_LIMIT)
        .all()
    )
    return [
        {"role": r.role, "message": r.message, "created_at": r.created_at.isoformat()} for r in reversed(rows)
    ]


class MyTutorSendRequest(BaseModel):
    message: str


@router.post("/admin/my-tutor/chat/send")
async def send_my_tutor_message(
    body: MyTutorSendRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    student = _my_tutor_student(db, teacher)
    return await _my_tutor_reply(db, student, body.message)


@router.post("/admin/my-tutor/chat/send-image")
async def send_my_tutor_image(
    file: UploadFile = File(...), db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    """Same photo-OCR pipeline as the student app's send-image (see app.routers.student_app)."""
    student = _my_tutor_student(db, teacher)
    if not student.has_feature("ocr"):
        raise HTTPException(status_code=403, detail="Photo questions aren't available on your personal tutor account yet")
    if not cost_tracker.has_credits(db, student.id):
        raise HTTPException(status_code=402, detail=_my_tutor_credits_exhausted_detail(student))
    image_bytes = await file.read()
    message_text = await extract_text_from_image(image_bytes)
    if not message_text:
        raise HTTPException(status_code=422, detail="Couldn't read any text in that photo — try a clearer picture")
    cost_tracker.record_flat_usage(db, "azure_ocr", student.id)
    return await _my_tutor_reply(db, student, message_text)


@router.post("/admin/my-tutor/chat/send-voice")
async def send_my_tutor_voice(
    file: UploadFile = File(...), db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    """Same Sarvam STT pipeline as the student app's send-voice (see app.routers.student_app)."""
    student = _my_tutor_student(db, teacher)
    if not student.has_feature("voice"):
        raise HTTPException(status_code=403, detail="Voice questions aren't available on your personal tutor account yet")
    if not cost_tracker.has_credits(db, student.id):
        raise HTTPException(status_code=402, detail=_my_tutor_credits_exhausted_detail(student))
    audio_bytes = await file.read()
    message_text = await transcribe_audio(audio_bytes, filename=file.filename or "voice_note.m4a")
    if not message_text:
        raise HTTPException(status_code=422, detail="Couldn't understand that voice note — please try again")
    cost_tracker.record_minute_usage(db, "sarvam_stt", get_duration_seconds(audio_bytes) / 60, student.id)
    if student.gender is None:
        detected_gender = detect_gender_from_pitch(audio_bytes)
        if detected_gender:
            student.gender = detected_gender
            db.commit()
    return await _my_tutor_reply(db, student, message_text)


@router.post("/admin/my-tutor/chat/send-document")
async def send_my_tutor_document(
    file: UploadFile = File(...), db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    """Same document-extraction pipeline as the student app's send-document (see app.routers.student_app)."""
    student = _my_tutor_student(db, teacher)
    if not student.has_feature("documents"):
        raise HTTPException(status_code=403, detail="PDF/Word file questions aren't available on your personal tutor account yet")
    if not cost_tracker.has_credits(db, student.id):
        raise HTTPException(status_code=402, detail=_my_tutor_credits_exhausted_detail(student))
    document_bytes = await file.read()
    message_text = extract_text_from_document(document_bytes, file.filename or "")
    if not message_text:
        raise HTTPException(status_code=422, detail="Couldn't read any text in that file — try a different file")
    return await _my_tutor_reply(db, student, message_text)


class ActivateTutorSubscriptionRequest(BaseModel):
    duration_days: int = cost_tracker.UNLIMITED_TEACHER_MONTHLY_DAYS
    is_trial: bool = False
    payment_reference: str | None = None  # required unless is_trial — see SetSubscriptionRequest
    note: str | None = None


@router.post("/admin/teachers/{teacher_id}/my-tutor-subscription/activate")
def activate_teacher_tutor_subscription(
    teacher_id: int, body: ActivateTutorSubscriptionRequest, db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    """
    Activates a teacher's own personal-tutor unlimited plan (₹3500/mo,
    prorated if duration_days differs from the standard 30). Only Qlass's
    super_admin can call this (same manual-activation pattern as
    set_student_subscription) — targets teacher_id explicitly rather than
    the caller's own profile, since it's Qlass staff activating it for a
    teacher after that teacher's payment lands, not a self-serve action.
    Records a real ledger entry the same way set_student_subscription does
    — see _activate_unlimited.
    """
    if teacher.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Qlass staff can activate the personal tutor plan")
    if not body.is_trial:
        if not body.payment_reference:
            raise HTTPException(status_code=400, detail="payment_reference is required for a paid activation")
        if cost_tracker.has_processed_external_ref(db, body.payment_reference):
            raise HTTPException(status_code=409, detail="This payment reference has already been used")
    target_teacher = db.query(Teacher).filter(Teacher.id == teacher_id).first()
    if target_teacher is None:
        raise HTTPException(status_code=404, detail="Teacher not found")
    student = _my_tutor_student(db, target_teacher)
    _activate_unlimited(
        db, student, body.duration_days, body.is_trial, body.payment_reference, body.note,
        cost_tracker.UNLIMITED_TEACHER_MONTHLY_PRICE, cost_tracker.UNLIMITED_TEACHER_MONTHLY_DAYS,
        teacher.id,
    )
    db.commit()
    return {"subscription_plan": student.subscription_plan, "subscription_expires_at": student.subscription_expires_at}


@router.get("/admin/analytics")
def get_analytics(
    centre_id: int | None = None, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    """
    Analytics is per-school, so org_admin/super_admin (who each span
    multiple schools) must say which one via centre_id — same resolution
    as the school profile/statement endpoints. A school's own admin always
    sees their own school regardless of what's passed.
    """
    if teacher.role not in ("admin", "org_admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can view school analytics")
    centre = _resolve_centre_for_read(db, teacher, centre_id)
    student_ids = [
        row.id for row in _scoped_students(db, teacher).filter(Student.centre_id == centre.id).with_entities(Student.id).all()
    ]
    return get_school_analytics(db, student_ids, centre.id)


@router.get("/admin/school/statement")
def get_school_statement(
    year: int, month: int, centre_id: int | None = None,
    db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    """
    Monthly billing statement PDF for the school's own credit_events ledger
    (teacher-tool spend, not per-student wallets — see school_billing).
    centre_id is required for org_admin/super_admin, same resolution as
    analytics/school-profile — previously this used teacher.centre_id
    directly, which is None for those roles and crashed rather than erroring
    cleanly (Centre.id == None matches no row, so centre came back None and
    generate_school_statement_pdf blew up on centre.name).
    """
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="month must be 1-12")
    centre = _resolve_centre_for_read(db, teacher, centre_id)
    pdf_bytes = generate_school_statement_pdf(db, centre, year, month)
    filename = f"{centre.name.replace(' ', '_')}_statement_{year}_{month:02d}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/admin/deletion-requests")
def get_deletion_requests(db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)):
    """
    Pending data-erasure requests a parent submitted via the parent app
    (see /parent-app/request-deletion) — a school's own admin sees their
    school's requests, org_admin sees every school in their own
    organization, super_admin sees all, matching the usual scoping (see
    _scoped_students — used here instead of list_pending_deletion_requests's
    own single-centre_id filter, since that would need a None centre_id to
    mean "no filter" for org_admin too, which would incorrectly also
    surface every OTHER organization's/school's pending requests).
    """
    if teacher.role not in ("admin", "org_admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can view deletion requests")
    students = (
        _scoped_students(db, teacher)
        .filter(Student.deletion_requested_at.isnot(None))
        .order_by(Student.deletion_requested_at.asc())
        .all()
    )
    return [
        {"id": s.id, "name": s.name, "phone": s.phone, "requested_at": s.deletion_requested_at}
        for s in students
    ]


@router.post("/admin/students/{student_id}/fulfill-deletion")
def fulfill_deletion(student_id: int, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)):
    """
    Irreversibly erases a student's PII/content per a pending deletion
    request. Restricted to super_admin — same reasoning as manual credit
    grants (see add_student_credits): a destructive, hard-to-reverse action
    shouldn't be self-serve for a school's own admin.
    """
    if teacher.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Qlass staff can fulfill a deletion request")
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    if student.deletion_requested_at is None:
        raise HTTPException(status_code=400, detail="No pending deletion request for this student")
    # Captured before the irreversible delete below, so the audit row still
    # has something recognizable to point at — the student row itself won't
    # exist to look this up afterward.
    detail = f"name={student.name} phone={student.phone} centre_id={student.centre_id}"
    fulfill_deletion_request(db, student_id)
    audit_log.record(db, teacher.id, "delete_student", "student", student_id, detail=detail)
    return {"deleted": True}


@router.get("/admin/audit-log")
def get_audit_log(
    limit: int = QueryParam(default=100, ge=1, le=500),
    offset: int = QueryParam(default=0, ge=0),
    db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    """Qlass-staff-only view of every recorded high-blast-radius action —
    see AuditLog's docstring for what gets logged here."""
    if teacher.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Qlass staff can view the audit log")
    rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()
    actor_ids = {r.actor_teacher_id for r in rows if r.actor_teacher_id is not None}
    actors = {t.id: t.name for t in db.query(Teacher).filter(Teacher.id.in_(actor_ids)).all()}
    return [
        {
            "id": r.id, "actor_teacher_id": r.actor_teacher_id, "actor_name": actors.get(r.actor_teacher_id),
            "action": r.action, "target_type": r.target_type, "target_id": r.target_id,
            "detail": r.detail, "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/admin/schools")
def get_schools(db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)):
    if teacher.role == "super_admin":
        return get_schools_overview(db)
    if teacher.role == "org_admin":
        return get_schools_overview(db, organization_id=teacher.organization_id)
    raise HTTPException(status_code=403, detail="Only Qlass staff or an organization admin can view the schools/sales pipeline")


class UpdateSalesInfoRequest(BaseModel):
    sales_status: str | None = None  # "prospect" | "trial" | "active" | "churned"
    sales_notes: str | None = None
    contract_notes: str | None = None


@router.patch("/admin/schools/{centre_id}/sales")
async def update_school_sales_info(
    centre_id: int, body: UpdateSalesInfoRequest, db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    if body.sales_status is not None and body.sales_status not in ("prospect", "trial", "active", "churned"):
        raise HTTPException(status_code=400, detail="sales_status must be prospect, trial, active, or churned")
    centre = db.query(Centre).filter(Centre.id == centre_id).first()
    if centre is None:
        raise HTTPException(status_code=404, detail="School not found")
    _require_school_management_access(teacher, centre)
    was_churned = centre.sales_status == "churned"
    if body.sales_status is not None:
        centre.sales_status = body.sales_status
    if body.sales_notes is not None:
        centre.sales_notes = body.sales_notes
    if body.contract_notes is not None:
        centre.contract_notes = body.contract_notes
    db.commit()
    # Previously silent: every student at a churned school just hit a
    # paywall on their next message with no warning to anyone at the
    # school. Notify the school's own staff at the moment of transition (in
    # both directions) so they aren't finding out from a parent complaint —
    # not individual students/parents directly, since Qlass's relationship
    # is with the school, not a mass alert to every enrolled family.
    now_churned = centre.sales_status == "churned"
    if now_churned != was_churned:
        recipients = get_escalation_recipients(db, centre.id)
        if now_churned:
            notice = (
                f"⚠️ {centre.name}'s Qlass account has been marked churned — students here will no "
                f"longer get AI tutor replies on WhatsApp starting now (any student with their own "
                f"paid credits keeps working). Contact Qlass to reactivate."
            )
        else:
            notice = f"✅ {centre.name}'s Qlass account is active again — students here can resume chatting on WhatsApp."
        for recipient in recipients:
            try:
                await send_whatsapp_message(recipient.phone, notice)
            except Exception:
                pass
    return {
        "id": centre.id, "sales_status": centre.sales_status,
        "sales_notes": centre.sales_notes, "contract_notes": centre.contract_notes,
    }
