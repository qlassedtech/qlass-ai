import csv
import io
import json
from datetime import datetime, timedelta, timezone

import razorpay
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query as QueryParam
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.query import Query

from app.config import settings
from app.database import get_db
from app.models.core import Centre, Chapter, ChatHistory, Parent, Question, Quiz, Student, Subject, Teacher
from app.services import cost_tracker, school_billing
from app.services.school_pilot import PILOT_STUDENT_FEATURES, launch_pilot, pilot_outcome_report
from app.services.analytics import get_school_analytics
from app.services.deletion import fulfill_deletion_request, list_pending_deletion_requests
from app.services.otp import generate_and_store_otp, verify_otp
from app.services.quiz_service import generate_quiz_questions
from app.services.sales import get_schools_overview
from app.services.school_statement import generate_school_statement_pdf
from app.services.rate_limit import is_otp_rate_limited
from app.services.pdf_render import render_workbook_pdf
from app.services.progress_report import get_student_stats, get_activity_stats, get_chapter_coverage, format_teacher_digest
from app.services.gamma_service import create_presentation_generation, get_generation_status
from app.services import razorpay_client
from app.services.razorpay_client import client as _razorpay_client, MIN_TOPUP_AMOUNT
from app.services.student_chat import process_web_message
from app.services.teacher_auth import get_current_teacher, hash_password, verify_password, create_access_token
from app.services.tenancy import get_or_create_linked_student
from app.services.uploads import save_image_upload
from app.services.whatsapp_client import send_whatsapp_message
from app.services.workbook_service import generate_workbook_questions

router = APIRouter()

DEFAULT_FEATURES = {"voice": False, "ocr": False, "image_generation": False, "documents": False, "youtube_videos": False}


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
    class_: str | None = None
    board: str | None = None
    school: str | None = None
    focus_topic: str | None = None
    features: dict | None = None
    parent_phone: str | None = None
    parent_name: str | None = None


def _student_to_dict(db: Session, student: Student) -> dict:
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
        "credit_balance": cost_tracker.get_balance(db, student.id),
        "centre_id": student.centre_id,
        "referral_code": student.referral_code,
        "referral_credits_earned": cost_tracker.get_referral_credits_earned(db, student.id),
        "photo_url": student.photo_url,
        "parent_phone": parent.phone if parent else None,
        "parent_name": parent.name if parent else None,
        "subscription_plan": student.subscription_plan,
        "subscription_expires_at": student.subscription_expires_at,
    }


def _scoped_students(db: Session, teacher: Teacher) -> Query:
    """
    This product is sold to multiple schools — a school's own admin/teacher
    must only ever see their own school's (centre's) students, never
    another school's. Only "super_admin" (Qlass's own staff) sees across
    every school. Also excludes teachers' own personal "My AI Tutor"
    profiles (see tenancy.get_or_create_linked_student) — those aren't
    real students and shouldn't appear in any student-facing list. And
    excludes students whose data-deletion request has been fulfilled (see
    app.services.deletion) — that row is kept (anonymized) only for the
    credit_events audit trail, not to be shown as a live student.
    """
    query = db.query(Student).filter(Student.is_staff_profile.is_(False), Student.is_deleted.is_(False))
    if teacher.role != "super_admin":
        query = query.filter(Student.centre_id == teacher.centre_id)
    return query


def _get_scoped_student_or_404(db: Session, teacher: Teacher, student_id: int) -> Student:
    student = _scoped_students(db, teacher).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


@router.post("/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    teacher = db.query(Teacher).filter(Teacher.phone == body.phone).first()
    if not teacher or not teacher.password_hash or not verify_password(body.password, teacher.password_hash):
        raise HTTPException(status_code=401, detail="Invalid phone or password")
    token = create_access_token(teacher.id)
    return {"access_token": token, "teacher": {"id": teacher.id, "name": teacher.name, "role": teacher.role}}


class RegisterSchoolRequest(BaseModel):
    school_name: str
    city: str | None = None
    admin_name: str
    admin_phone: str
    password: str


@router.post("/auth/register-school")
def register_school(body: RegisterSchoolRequest, db: Session = Depends(get_db)):
    """
    Self-serve entry point for a new school signing up — creates both the
    school (Centre, the tenant boundary) and its first admin account in one
    step, since a brand-new school has no existing admin to invite them.
    Any additional teacher/admin accounts for this school are added
    afterward from the portal (see POST /admin/teachers), not through this
    endpoint again.
    """
    if db.query(Teacher).filter(Teacher.phone == body.admin_phone).first():
        raise HTTPException(status_code=409, detail="An account with this phone number already exists")

    centre = Centre(name=body.school_name, city=body.city)
    db.add(centre)
    db.commit()
    db.refresh(centre)
    school_billing.add_trial_credits(db, centre.id)

    teacher = Teacher(
        name=body.admin_name, phone=body.admin_phone,
        password_hash=hash_password(body.password), role="admin", centre_id=centre.id,
    )
    db.add(teacher)
    db.commit()
    db.refresh(teacher)

    token = create_access_token(teacher.id)
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
    teacher = db.query(Teacher).filter(Teacher.phone == body.phone).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Account not found")
    teacher.password_hash = hash_password(body.new_password)
    db.commit()
    return {"reset": True}


@router.get("/admin/me")
def me(teacher: Teacher = Depends(get_current_teacher)):
    return {
        "id": teacher.id, "name": teacher.name, "role": teacher.role, "phone": teacher.phone,
        "photo_url": teacher.photo_url,
    }


@router.get("/admin/students")
def list_students(
    limit: int = QueryParam(default=100, ge=1, le=500),
    offset: int = QueryParam(default=0, ge=0),
    db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    students = _scoped_students(db, teacher).order_by(Student.id).offset(offset).limit(limit).all()
    return [_student_to_dict(db, s) for s in students]


@router.post("/admin/students")
def create_student(
    body: StudentCreateRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    student = Student(
        name=body.name, phone=body.phone, class_=body.class_, board=body.board, school=body.school,
        features=dict(DEFAULT_FEATURES), centre_id=teacher.centre_id,
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    cost_tracker.add_trial_credits(db, student.id)
    return _student_to_dict(db, student)


@router.post("/admin/students/bulk-upload")
async def bulk_upload_students(
    file: UploadFile = File(...),
    features: str = Form(...),  # JSON-encoded dict, e.g. {"voice": true, "ocr": false, ...}
    db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    """
    CSV columns expected: name, phone, class, board, school (board/school
    optional, class optional). The same feature-access selection is applied
    to every student in the batch — for per-student feature differences,
    upload separate batches or adjust individually afterward on the
    student's detail page. Every created student is assigned to the
    uploading teacher's own school (centre) and starts with trial credits.
    """
    try:
        selected_features = {**DEFAULT_FEATURES, **json.loads(features)}
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid features payload")

    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    reader.fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]

    created, updated, skipped = [], [], []
    for row in reader:
        name = (row.get("name") or "").strip()
        phone = (row.get("phone") or "").strip()
        if not name or not phone:
            skipped.append(row)
            continue

        # Scoped to the uploader's own school — matching by phone alone
        # would let one school's bulk upload silently overwrite another
        # school's student just by coincidentally listing the same number.
        existing = (
            db.query(Student)
            .filter(Student.phone == phone, Student.centre_id == teacher.centre_id)
            .first()
        )
        if existing:
            existing.features = {**(existing.features or {}), **selected_features}
            if row.get("class"):
                existing.class_ = row["class"].strip()
            if row.get("board"):
                existing.board = row["board"].strip()
            if row.get("school"):
                existing.school = row["school"].strip()
            updated.append(phone)
        else:
            new_student = Student(
                name=name, phone=phone,
                class_=(row.get("class") or "").strip() or None,
                board=(row.get("board") or "").strip() or None,
                school=(row.get("school") or "").strip() or None,
                features=selected_features,
                centre_id=teacher.centre_id,
            )
            db.add(new_student)
            db.commit()
            db.refresh(new_student)
            cost_tracker.add_trial_credits(db, new_student.id)
            created.append(phone)

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
    if body.class_ is not None:
        student.class_ = body.class_
    if body.board is not None:
        student.board = body.board
    if body.school is not None:
        student.school = body.school
    if body.focus_topic is not None:
        student.focus_topic = body.focus_topic
    if body.features is not None:
        student.features = {**(student.features or {}), **body.features}
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
    link = f"{settings.portal_base_url}/pay?phone={student.phone}"
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
    return {"balance": new_balance}


TRIAL_UNLIMITED_SERVICE = "unlimited_plan_trial"
PAID_UNLIMITED_SERVICE = "unlimited_plan_activation"


def _activate_unlimited(
    db: Session, student: Student, duration_days: int, is_trial: bool,
    payment_reference: str | None, note: str | None, full_term_price: float, full_term_days: int,
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
    """
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
    Manually activates the flat-fee unlimited plan (₹1800/yr, prorated if
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
    people before committing to paying for everyone. Super_admin-only, and
    always a trial (see _activate_unlimited) — no payment_reference, and
    tagged so it correctly does NOT count as an independent payment for
    churn-gating purposes.
    """
    if teacher.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Qlass staff can grant trial subscriptions")
    if not body.student_ids and not body.teacher_ids:
        raise HTTPException(status_code=400, detail="Select at least one student or teacher")
    centre = db.query(Centre).filter(Centre.id == centre_id).first()
    if centre is None:
        raise HTTPException(status_code=404, detail="School not found")

    activated = []
    for student in (
        db.query(Student)
        .filter(Student.id.in_(body.student_ids), Student.centre_id == centre_id, Student.is_staff_profile.is_(False))
        .all()
    ):
        _activate_unlimited(
            db, student, body.duration_days, True, None, body.note,
            cost_tracker.UNLIMITED_STUDENT_ANNUAL_PRICE, cost_tracker.UNLIMITED_STUDENT_ANNUAL_DAYS,
        )
        activated.append(student.name)

    for target_teacher in db.query(Teacher).filter(Teacher.id.in_(body.teacher_ids), Teacher.centre_id == centre_id).all():
        my_tutor_student = _my_tutor_student(db, target_teacher)
        _activate_unlimited(
            db, my_tutor_student, body.duration_days, True, None, body.note,
            cost_tracker.UNLIMITED_TEACHER_MONTHLY_PRICE, cost_tracker.UNLIMITED_TEACHER_MONTHLY_DAYS,
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
    """Fund a bounded, no-charge school pilot with student wallet credits."""
    if teacher.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Qlass staff can launch school pilots")
    if not body.student_ids:
        raise HTTPException(status_code=400, detail="Select at least one student for the pilot")
    centre = db.query(Centre).filter(Centre.id == centre_id).first()
    if centre is None:
        raise HTTPException(status_code=404, detail="School not found")
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
    if teacher.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can view teacher accounts")
    query = db.query(Teacher)
    if teacher.role != "super_admin":
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
def create_teacher(
    body: TeacherCreateRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    """
    Lets a school's own admin add teacher accounts for their school without
    needing CLI/database access — necessary now that schools can self-
    register (see /auth/register-school), since a non-technical school
    admin has no other way to provision their staff's logins.
    """
    if teacher.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can add teacher accounts")
    if body.role not in ("teacher", "admin"):
        raise HTTPException(status_code=400, detail="Role must be 'teacher' or 'admin'")
    if db.query(Teacher).filter(Teacher.phone == body.phone).first():
        raise HTTPException(status_code=409, detail="An account with this phone number already exists")

    if teacher.role == "admin":
        centre_id = teacher.centre_id
    else:
        if not body.centre_id:
            raise HTTPException(status_code=400, detail="centre_id is required")
        centre_id = body.centre_id

    new_teacher = Teacher(
        name=body.name, phone=body.phone, password_hash=hash_password(body.password),
        role=body.role, centre_id=centre_id,
    )
    db.add(new_teacher)
    db.commit()
    db.refresh(new_teacher)
    return _teacher_to_dict(new_teacher)


def _resolve_centre_for_read(db: Session, teacher: Teacher, centre_id: int | None) -> Centre:
    """
    Any signed-in teacher/admin can VIEW their own school's profile and
    credit balance (needed for the workbook generator to show remaining
    balance) — only WRITE actions (logo, manual credit grants) are
    restricted further, see _resolve_centre_for_write below.
    """
    target_id = teacher.centre_id if teacher.role != "super_admin" else centre_id
    if not target_id:
        raise HTTPException(status_code=400, detail="centre_id is required")
    centre = db.query(Centre).filter(Centre.id == target_id).first()
    if not centre:
        raise HTTPException(status_code=404, detail="School not found")
    return centre


def _resolve_centre_for_write(db: Session, teacher: Teacher, centre_id: int | None) -> Centre:
    """
    A school's own admin always edits their own school; only super_admin
    (Qlass staff, who has no centre of their own) may target a different
    school, and must say which one via centre_id — mirrors the same pattern
    used for admin-vs-super_admin teacher creation above.
    """
    if teacher.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can manage the school profile")
    return _resolve_centre_for_read(db, teacher, centre_id)


@router.get("/admin/school")
def get_school(
    centre_id: int | None = None, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    centre = _resolve_centre_for_read(db, teacher, centre_id)
    return {
        "id": centre.id, "name": centre.name, "city": centre.city, "logo_url": centre.logo_url,
        "credit_balance": school_billing.get_balance(db, centre.id),
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


class WorkbookGenerateRequest(BaseModel):
    topic: str
    class_: str | None = None
    num_questions: int = 10
    include_answer_key: bool = True


@router.post("/admin/workbook/generate")
async def generate_workbook(
    body: WorkbookGenerateRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    """
    Teacher-facing practice-set PDF generator — billed to the school's own
    credit ledger (school_billing), not any student's wallet, since this is
    a teacher tool rather than tutoring usage.
    """
    if teacher.role == "super_admin":
        raise HTTPException(status_code=400, detail="Sign in as a school's own teacher/admin to generate a workbook")
    if not school_billing.has_credits(db, teacher.centre_id):
        raise HTTPException(status_code=402, detail="Your school is out of credits for workbook generation")

    centre = db.query(Centre).filter(Centre.id == teacher.centre_id).first()

    questions, llm_result = await generate_workbook_questions(body.topic, body.class_, body.num_questions)
    school_billing.record_claude_usage(
        db, teacher.centre_id, "workbook_pdf", llm_result.input_tokens, llm_result.output_tokens
    )
    if not questions:
        raise HTTPException(status_code=502, detail="Couldn't generate questions for that topic — try again")

    pdf_bytes = render_workbook_pdf(
        topic=body.topic, class_=body.class_, school_name=centre.name, school_logo_url=centre.logo_url,
        questions=questions, include_answer_key=body.include_answer_key,
    )
    filename = f"{body.topic.replace(' ', '_')}_worksheet.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/admin/curriculum/chapters")
def list_curriculum_chapters(
    class_: str, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    """
    Chapters for the seeded NCERT curriculum (see scripts/seed_ncert_curriculum.py),
    grouped by subject — lets a teacher assign a quiz tied to an actual
    chapter instead of a free-text topic prone to typos/ambiguity.
    Curriculum data isn't centre-scoped (it's the shared national syllabus),
    so any authenticated teacher/admin can browse it.
    """
    rows = (
        db.query(Chapter, Subject)
        .join(Subject, Chapter.subject_id == Subject.id)
        .filter(Subject.class_ == class_)
        .order_by(Subject.name, Chapter.chapter_no)
        .all()
    )
    return [
        {"id": chapter.id, "name": chapter.name, "chapter_no": chapter.chapter_no, "subject": subject.name}
        for chapter, subject in rows
    ]


class AssignQuizRequest(BaseModel):
    topic: str | None = None
    chapter_id: int | None = None  # takes priority over topic when both are given
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

    chapter = None
    if body.chapter_id:
        chapter = db.query(Chapter).filter(Chapter.id == body.chapter_id).first()
        if chapter is None:
            raise HTTPException(status_code=404, detail="Chapter not found")
    topic = chapter.name if chapter else body.topic
    if not topic:
        raise HTTPException(status_code=400, detail="Either topic or chapter_id is required")

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

    questions_data, gen_result = await generate_quiz_questions(topic, body.class_)
    school_billing.record_claude_usage(
        db, teacher.centre_id, "quiz_assignment", gen_result.input_tokens, gen_result.output_tokens
    )
    if not questions_data:
        raise HTTPException(status_code=502, detail="Couldn't generate questions for that topic — try again")

    assigned, skipped = [], []
    for student in targets:
        if student.active_quiz_id:
            skipped.append(student.name)  # already mid-quiz — don't interrupt it with a new one
            continue
        quiz = Quiz(
            student_id=student.id, created_by_teacher_id=teacher.id, title=topic,
            chapter_id=chapter.id if chapter else None,
        )
        db.add(quiz)
        db.commit()
        db.refresh(quiz)
        for q in questions_data:
            db.add(Question(
                quiz_id=quiz.id, question_type=q.get("question_type", "short_answer"),
                question_text=q["question"], correct_answer=q["answer"],
            ))
        db.commit()
        student.active_quiz_id = quiz.id
        db.commit()
        intro = (
            f"📝 Your teacher assigned a {len(questions_data)}-question quiz on *{topic}*!\n\n"
            f"Question 1/{len(questions_data)}: {questions_data[0]['question']}"
        )
        await send_whatsapp_message(student.phone, intro)
        assigned.append(student.name)

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
    topic: str
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
    """
    if teacher.role == "super_admin":
        raise HTTPException(status_code=400, detail="Sign in as a school's own teacher/admin to generate a presentation")
    if not settings.gamma_api_key:
        raise HTTPException(status_code=503, detail="Presentation generation isn't configured yet — contact Qlass support")
    if not school_billing.has_credits(db, teacher.centre_id):
        raise HTTPException(status_code=402, detail="Your school is out of credits for presentation generation")

    generation_id = await create_presentation_generation(body.topic, body.num_cards)
    return {"generation_id": generation_id}


@router.get("/admin/presentation/status/{generation_id}")
async def presentation_status(
    generation_id: str, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    if not settings.gamma_api_key:
        raise HTTPException(status_code=503, detail="Presentation generation isn't configured yet — contact Qlass support")
    result = await get_generation_status(generation_id)

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
    }


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
        .order_by(ChatHistory.created_at.asc())
        .all()
    )
    return [{"role": r.role, "message": r.message, "created_at": r.created_at.isoformat()} for r in rows]


class MyTutorSendRequest(BaseModel):
    message: str


@router.post("/admin/my-tutor/chat/send")
async def send_my_tutor_message(
    body: MyTutorSendRequest, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)
):
    student = _my_tutor_student(db, teacher)
    if not cost_tracker.has_credits(db, student.id):
        raise HTTPException(status_code=402, detail="You're out of AI credits for your personal tutor account")
    reply = await process_web_message(db, student, body.message)
    return {"reply": reply, "credit_balance": cost_tracker.get_balance(db, student.id)}


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
    )
    db.commit()
    return {"subscription_plan": student.subscription_plan, "subscription_expires_at": student.subscription_expires_at}


@router.get("/admin/analytics")
def get_analytics(db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)):
    if teacher.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can view school analytics")
    if teacher.role == "super_admin" and teacher.centre_id is None:
        raise HTTPException(status_code=400, detail="Sign in as a school's own admin to view analytics")
    student_ids = [s.id for s in _scoped_students(db, teacher).all()]
    return get_school_analytics(db, student_ids, teacher.centre_id)


@router.get("/admin/school/statement")
def get_school_statement(
    year: int, month: int, db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher),
):
    """
    Monthly billing statement PDF for the school's own credit_events ledger
    (teacher-tool spend, not per-student wallets — see school_billing).
    """
    if teacher.role == "super_admin":
        raise HTTPException(status_code=400, detail="Sign in as a school's own admin to download its statement")
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="month must be 1-12")
    centre = db.query(Centre).filter(Centre.id == teacher.centre_id).first()
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
    school's requests, super_admin sees all, matching the usual scoping.
    """
    if teacher.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can view deletion requests")
    centre_id = None if teacher.role == "super_admin" else teacher.centre_id
    students = list_pending_deletion_requests(db, centre_id)
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
    fulfill_deletion_request(db, student_id)
    return {"deleted": True}


@router.get("/admin/schools")
def get_schools(db: Session = Depends(get_db), teacher: Teacher = Depends(get_current_teacher)):
    if teacher.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Qlass staff can view the schools/sales pipeline")
    return get_schools_overview(db)


class UpdateSalesInfoRequest(BaseModel):
    sales_status: str | None = None  # "prospect" | "trial" | "active" | "churned"
    sales_notes: str | None = None
    contract_notes: str | None = None


@router.patch("/admin/schools/{centre_id}/sales")
def update_school_sales_info(
    centre_id: int, body: UpdateSalesInfoRequest, db: Session = Depends(get_db),
    teacher: Teacher = Depends(get_current_teacher),
):
    if teacher.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Qlass staff can edit the schools/sales pipeline")
    if body.sales_status is not None and body.sales_status not in ("prospect", "trial", "active", "churned"):
        raise HTTPException(status_code=400, detail="sales_status must be prospect, trial, active, or churned")
    centre = db.query(Centre).filter(Centre.id == centre_id).first()
    if centre is None:
        raise HTTPException(status_code=404, detail="School not found")
    if body.sales_status is not None:
        centre.sales_status = body.sales_status
    if body.sales_notes is not None:
        centre.sales_notes = body.sales_notes
    if body.contract_notes is not None:
        centre.contract_notes = body.contract_notes
    db.commit()
    return {
        "id": centre.id, "sales_status": centre.sales_status,
        "sales_notes": centre.sales_notes, "contract_notes": centre.contract_notes,
    }
