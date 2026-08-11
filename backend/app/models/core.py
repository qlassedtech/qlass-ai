from sqlalchemy import (
    Column, DDL, Integer, Text, Boolean, Numeric, Date, JSON, CheckConstraint, ForeignKey, TIMESTAMP,
    UniqueConstraint, event, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database import Base

# JSONB in production (Postgres); falls back to generic JSON for other dialects (e.g. sqlite in tests)
JSONType = JSONB().with_variant(JSON(), "sqlite")


class Organization(Base):
    """
    A group of schools/centres under one umbrella account — e.g. a state
    government programme spanning many schools, or a multi-branch private
    chain. Distinct from a single Centre (school): an org_admin (see
    Teacher.role) manages every centre under their organization_id, the
    same way a super_admin manages every centre on the whole platform, just
    scoped to their own organization instead of everything.
    """
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    # "government" | "school_group" — informational only for now, doesn't
    # change any access-control behaviour.
    org_type = Column(Text, default="school_group")
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    centres = relationship("Centre", back_populates="organization")


class Centre(Base):
    __tablename__ = "centres"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    city = Column(Text)
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    # Shown on the school's own portal header and stamped onto anything
    # generated on their behalf (e.g. practice-set PDFs) alongside the
    # "Powered by Qlass Learning" mark.
    logo_url = Column(Text)
    # The school's own board (e.g. "CBSE", "BSEB") — a school already knows
    # which board it follows, so new students under it default to this
    # instead of being asked individually (see tenancy.py/whatsapp.py). A
    # school with genuinely mixed-board sections can still override per
    # student; this is just the sensible default, not an enforced value.
    board = Column(Text)
    # Whether a student self-registering through THIS school's own link
    # (see app.routers.public.register) becomes a full roster member
    # immediately (true, default) or starts "pending" until a teacher
    # confirms them (false — see Student.approval_status and
    # GET /admin/students/pending). A school's own choice, not a platform
    # default — set via PATCH /admin/school.
    auto_approve_students = Column(Boolean, default=True)
    # Lightweight sales-pipeline tracking — "prospect" (being sold to, no
    # real usage yet) | "trial" | "active" | "churned". Self-registered
    # schools (see /auth/register-school) start as "active" since they've
    # already begun using the product; Qlass-side pipeline entries created
    # ahead of an actual signup would start as "prospect".
    sales_status = Column(Text, default="active")
    sales_notes = Column(Text)
    pilot_status = Column(Text, default="none")
    pilot_started_at = Column(TIMESTAMP(timezone=True))
    pilot_expires_at = Column(TIMESTAMP(timezone=True))
    # Free-text record of a negotiated contract (e.g. "₹50,000/year
    # unlimited, signed 2026-04-01") — not automated billing, just so a
    # custom deal is represented somewhere instead of purely in someone's
    # inbox.
    contract_notes = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    students = relationship("Student", back_populates="centre")
    teachers = relationship("Teacher", back_populates="centre")
    organization = relationship("Organization", back_populates="centres")


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    # Not unique — a shared family phone can have more than one student
    # profile; see app.services.active_profile for how the active one is
    # resolved per incoming message.
    phone = Column(Text, nullable=False, index=True)
    # Set only when a school gives this student a portal password — the
    # normal path (WhatsApp OTP) needs no password at all, this exists
    # specifically for a student with no WhatsApp access (see
    # POST /admin/students/{id}/set-password and the student-app
    # /auth/login endpoint). Never set by a student themselves.
    password_hash = Column(Text)
    # An alternate real WhatsApp number for this student, when their
    # primary/login `phone` above isn't itself on WhatsApp (e.g. a parent's
    # or a different personal number is what they actually message from).
    # app.routers.whatsapp._resolve_active_student matches an inbound
    # message against EITHER phone or whatsapp_phone — `phone` stays the
    # one identity used for portal login/display, this is purely a second
    # routing key. Set via PATCH /admin/students/{id}.
    whatsapp_phone = Column(Text, index=True)
    # "approved" (default — every admin/teacher-provisioned student, and
    # every student that existed before this column did) or "pending" — a
    # student who self-registered through a specific school's link (see
    # app.routers.public.register) starts as "pending" until a teacher
    # confirms them via POST /admin/students/{id}/approve. Not a hard gate
    # on chatting — purely a review/visibility flag, see
    # GET /admin/students/pending.
    approval_status = Column(Text, default="approved")
    class_ = Column("class", Text)
    board = Column(Text)
    school = Column(Text)
    preferred_language = Column(Text, default="en-IN")
    centre_id = Column(Integer, ForeignKey("centres.id"))
    pending_profile_field = Column(Text)
    state = Column(Text, default="Bihar")
    features = Column(
        JSONType,
        default=lambda: {
            "voice": False, "ocr": False, "image_generation": False, "documents": False, "youtube_videos": False
        },
    )
    off_level_count = Column(Integer, default=0)
    suggested_class = Column(Text)
    gender = Column(Text)
    active_document_text = Column(Text)
    active_quiz_id = Column(Integer, ForeignKey("quizzes.id"))
    focus_topic = Column(Text)
    # Auto-updated on every real tutoring turn from the LLM's own topic
    # classification — NOT the same as focus_topic above (that's a
    # teacher-set steering field). Used to resolve "quiz on the same"/"quiz
    # on this" to what's actually being discussed right now. Deliberately
    # separate from TopicProgress (only written when a scored check
    # question is evaluated — a plain explanatory turn never writes there,
    # so relying on TopicProgress alone can resolve to a stale topic from
    # an unrelated earlier session when the current topic and a quiz
    # request arrive in the same message).
    last_discussed_topic = Column(Text)
    hints_given_count = Column(Integer, default=0)
    direct_solutions_count = Column(Integer, default=0)
    # Consecutive hint-only turns (solved_directly is False) since the last
    # time a problem was actually solved — reset to 0 on a direct solve or
    # right after an escalation fires. See app.services.escalation: once
    # this crosses ESCALATION_THRESHOLD, the student's teacher gets a
    # WhatsApp nudge that this student may need in-person help.
    consecutive_unresolved_hints = Column(Integer, default=0)
    # Manually uploaded from the portal — WhatsApp's Business API doesn't
    # expose a contact's profile photo to a business account (Meta blocks
    # this for user privacy), so there's no way to pull it in automatically.
    photo_url = Column(Text)
    # Firebase Cloud Messaging registration token for the native student app
    # (see app.services.push_client) — null for every WhatsApp-only student,
    # and for app users until they've logged in on a build with Firebase
    # actually configured (see android/app/build.gradle.kts FCM_* fields).
    fcm_token = Column(Text)
    referral_code = Column(Text, unique=True)
    referred_by_id = Column(Integer, ForeignKey("students.id"))
    # Which of app.services.referral.REFERRAL_MILESTONES have already paid
    # the referrer for THIS referred student (e.g. ["day1", "week2"]) — a
    # list rather than one boolean since there are several distinct
    # milestones now, each payable at most once.
    referral_milestones_paid = Column(JSONType, default=list)
    # Which of app.services.habit.HABIT_MILESTONES this student has already
    # earned (see evaluate_habit_milestones) — a 21-day engagement-building
    # reward schedule, separate from referral credits.
    habit_milestones_paid = Column(JSONType, default=list)
    # True only for a teacher/admin's own personal "My AI Tutor" profile
    # (see app.services.tenancy.get_or_create_linked_student) — excluded
    # from the real Student Roster and other student-facing lists so it
    # never appears as if it were an enrolled student, and matched
    # specifically on lookup so it can never collide with a real student
    # who happens to share the same phone number.
    is_staff_profile = Column(Boolean, default=False)
    # "credits" (default, pay-as-you-go wallet) or "unlimited" (flat-fee
    # subscription — see app.services.cost_tracker for the actual gating).
    # A staff profile's own subscription is the ₹3500/month personal-tutor
    # plan; a real student's is the ₹2499/year plan — same two columns
    # serve both since the mechanism (bypass the wallet check while active)
    # is identical, just the price/duration differ.
    subscription_plan = Column(Text, default="credits")
    subscription_expires_at = Column(TIMESTAMP(timezone=True))
    # Set only when the unlimited plan was activated through Razorpay's
    # recurring Subscriptions API (see app.routers.payments' subscription
    # endpoints and app.routers.razorpay_webhook) rather than a manual
    # super_admin activation/trial grant — lets the webhook look up which
    # student a subscription.charged/cancelled event belongs to, and lets a
    # self-serve cancel action target the right Razorpay subscription.
    razorpay_subscription_id = Column(Text, unique=True)
    # School-controller attestation that parental consent was obtained for
    # this minor's data (chat history, academic performance, phone number)
    # — captured at enrollment time. See app.services.consent.
    consent_given_at = Column(TIMESTAMP(timezone=True))
    # Data deletion/retention request (a parent or the student asked Qlass
    # to erase this profile's PII) — see app.services.deletion. is_deleted
    # marks it as actually fulfilled; deletion_requested_at alone means
    # still pending review, since a request shouldn't erase data instantly
    # without a Qlass staff member confirming it (e.g. against a live fee
    # dispute or an ongoing school investigation).
    deletion_requested_at = Column(TIMESTAMP(timezone=True))
    is_deleted = Column(Boolean, default=False)
    # {"fun_fact": "2026-08-01T10:00:00+00:00", "feature_highlight": "...",
    # "social_proof": "..."} — last-sent timestamp per re-engagement nudge
    # type (see app.services.nudges), so the rotation never repeats the same
    # type back-to-back and scripts/send_engagement_nudges.py can skip a
    # type still inside its cooldown window.
    nudges_sent = Column(JSONType, default=dict)
    # A student can text "stop nudges"/"unsubscribe" (see app.routers.
    # whatsapp) to opt out of proactive re-engagement messages — never sent
    # once true. Doesn't affect real tutoring replies, only this one
    # unprompted-outreach feature.
    nudges_opt_out = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    def has_feature(self, name: str) -> bool:
        return bool((self.features or {}).get(name))

    centre = relationship("Centre", back_populates="students")
    chat_history = relationship("ChatHistory", back_populates="student")
    parent = relationship("Parent", back_populates="student", uselist=False)

    def as_profile_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "class": self.class_,
            "board": self.board,
            "school": self.school,
            "preferred_language": self.preferred_language,
            "focus_topic": self.focus_topic,
        }


class Parent(Base):
    __tablename__ = "parents"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    name = Column(Text)
    phone = Column(Text, unique=True)

    student = relationship("Student", back_populates="parent")


class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(Integer, primary_key=True)
    name = Column(Text)
    phone = Column(Text, unique=True)
    centre_id = Column(Integer, ForeignKey("centres.id"))
    # Only set for role="org_admin" — see Organization. Null for every
    # other role, including super_admin (which is platform-wide, not
    # scoped to one organization).
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    password_hash = Column(Text)
    # "teacher" | "admin" | "org_admin" | "super_admin".
    # "teacher"/"admin" are scoped to their own centre_id (school) — this
    # product is sold to multiple schools, so each school's students/
    # teachers must stay isolated from every other school's. "admin"
    # additionally manages other teacher accounts for their own school.
    # "org_admin" is scoped to organization_id instead of one centre_id —
    # sees/manages every school under that organization (e.g. a government
    # programme spanning many schools), centre_id is null for it.
    # "super_admin" is Qlass's own staff role and sees/manages across every
    # school on the whole platform, centre_id and organization_id are both
    # null for it.
    role = Column(Text, default="teacher")
    photo_url = Column(Text)

    centre = relationship("Centre", back_populates="teachers")
    organization = relationship("Organization")


class Subject(Base):
    __tablename__ = "subjects"
    # A (class_, name, board) triple can legitimately repeat with different
    # chapters — the same subject name under different boards has a
    # genuinely different syllabus (e.g. BSEB Class 10 Hindi != CBSE/NCERT
    # Class 10 Hindi), so board is part of what identifies a subject here,
    # not just informational.
    __table_args__ = (UniqueConstraint("class", "name", "board", name="uq_subjects_class_name_board"),)

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    class_ = Column("class", Text)
    # 'CBSE' (covers the seeded NCERT curriculum) | 'BSEB' | ... — existing
    # rows predate this column and are all NCERT/CBSE-aligned, hence the
    # default. See scripts/seed_ncert_curriculum.py and
    # scripts/seed_bseb_curriculum.py.
    board = Column(Text, default="CBSE")

    chapters = relationship("Chapter", back_populates="subject")


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"))
    name = Column(Text, nullable=False)
    chapter_no = Column(Integer)

    subject = relationship("Subject", back_populates="chapters")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    drive_file_id = Column(Text, unique=True)
    title = Column(Text)
    class_ = Column("class", Text)
    subject = Column(Text)
    chapter = Column(Text)
    board = Column(Text)
    language = Column(Text)
    difficulty = Column(Text)
    synced_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    chunks = relationship("DocumentChunk", back_populates="document")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"))
    chunk_index = Column(Integer)
    content = Column(Text)
    embedding_id = Column(Text)  # pointer into the vector store (Chroma) — unused; see content_tsv below

    document = relationship("Document", back_populates="chunks")


# app.services.retrieval's full-text search needs a generated tsvector
# column + GIN index (see database/migrations/0038_add_document_chunks_
# fulltext_search.sql) — added via a raw DDL event (execute_if
# dialect="postgresql") rather than a mapped Column(Computed(...)) on the
# class above, because `to_tsvector(...)` is Postgres-only SQL: a mapped
# Computed column would make Base.metadata.create_all emit that same DDL
# for the in-memory SQLite test database too (see tests/conftest.py's
# db_session fixture, used everywhere) and fail table creation outright,
# even for tests that never touch documents/document_chunks at all.
event.listen(
    DocumentChunk.__table__,
    "after_create",
    DDL(
        "ALTER TABLE document_chunks ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED"
    ).execute_if(dialect="postgresql"),
)
event.listen(
    DocumentChunk.__table__,
    "after_create",
    DDL("CREATE INDEX idx_document_chunks_content_tsv ON document_chunks USING GIN (content_tsv)")
    .execute_if(dialect="postgresql"),
)

# Voyage-embedding vector column for semantic retrieval (see
# app.services.embeddings/app.services.retrieval.fetch_semantic_candidates)
# — same "raw DDL event, not a mapped Column" reasoning as content_tsv
# above: pgvector's `vector` type is Postgres-only, and the pgvector
# Python/SQLAlchemy package isn't a dependency here, so this column is
# written/read via raw SQL (text()) rather than the ORM. Dimension (1024)
# must match settings.voyage_embedding_dimensions — see
# database/migrations/0041_add_document_chunk_embeddings.sql, which is the
# version of this DDL actually applied to any database that already
# existed before this column did (this event only fires for a table
# CREATEd fresh via Base.metadata.create_all, e.g. local dev/tests).
event.listen(
    DocumentChunk.__table__,
    "after_create",
    DDL("CREATE EXTENSION IF NOT EXISTS vector").execute_if(dialect="postgresql"),
)
event.listen(
    DocumentChunk.__table__,
    "after_create",
    DDL("ALTER TABLE document_chunks ADD COLUMN embedding vector(1024)").execute_if(dialect="postgresql"),
)
event.listen(
    DocumentChunk.__table__,
    "after_create",
    DDL("CREATE INDEX idx_document_chunks_embedding_hnsw ON document_chunks USING hnsw (embedding vector_cosine_ops)")
    .execute_if(dialect="postgresql"),
)


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    role = Column(Text)  # 'user' | 'assistant'
    message = Column(Text)
    agent = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (CheckConstraint("role IN ('user','assistant')"),)

    student = relationship("Student", back_populates="chat_history")


class Quiz(Base):
    __tablename__ = "quizzes"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    chapter_id = Column(Integer, ForeignKey("chapters.id"))
    # Set only for a quiz a teacher explicitly assigned to a class (see
    # app.routers.admin's /admin/quizzes/assign) — null for a student's own
    # ad-hoc "quiz me on X" request. `title` is the topic string shown in
    # the assignment UI; ad-hoc quizzes don't need one (the topic already
    # appears inline in the WhatsApp quiz-start message).
    created_by_teacher_id = Column(Integer, ForeignKey("teachers.id"))
    title = Column(Text)
    # A longer (see quiz_service.MOCK_TEST_QUESTION_COUNT), timed board-exam
    # style practice test rather than the usual 5-question ad-hoc quiz —
    # only changes how the completion message is formatted (score + elapsed
    # time vs just score), the turn-by-turn answer/grade flow is identical.
    is_mock_test = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    questions = relationship("Question", back_populates="quiz")


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True)
    quiz_id = Column(Integer, ForeignKey("quizzes.id"))
    question_type = Column(Text)
    question_text = Column(Text)
    correct_answer = Column(Text)

    quiz = relationship("Quiz", back_populates="questions")


class Answer(Base):
    __tablename__ = "answers"

    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("questions.id"))
    student_id = Column(Integer, ForeignKey("students.id"))
    given_answer = Column(Text)
    is_correct = Column(Boolean)


class Attendance(Base):
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    date = Column(Date)
    status = Column(Text)

    __table_args__ = (CheckConstraint("status IN ('present','absent','late')"),)


class Progress(Base):
    __tablename__ = "progress"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    chapter_id = Column(Integer, ForeignKey("chapters.id"))
    mastery_score = Column(Numeric)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    period = Column(Text)
    summary = Column(Text)
    generated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    recipient_phone = Column(Text)
    message = Column(Text)
    sent_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class StudyPlan(Base):
    __tablename__ = "study_plans"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    plan_json = Column(JSONType)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    amount = Column(Numeric)
    status = Column(Text)
    paid_at = Column(TIMESTAMP(timezone=True))


class StudySession(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    started_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    ended_at = Column(TIMESTAMP(timezone=True))


class TopicProgress(Base):
    __tablename__ = "topic_progress"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    topic = Column(Text, nullable=False)
    question_text = Column(Text)
    given_answer = Column(Text)
    is_correct = Column(Boolean)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class ProcessedWebhookMessage(Base):
    __tablename__ = "processed_webhook_messages"

    message_id = Column(Text, primary_key=True)
    payload = Column(JSONType)
    status = Column(Text, nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text)
    lease_expires_at = Column(TIMESTAMP(timezone=True))
    processed_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class CreditEvent(Base):
    __tablename__ = "credit_events"

    id = Column(Integer, primary_key=True)
    amount = Column(Numeric, nullable=False)  # positive = top-up, negative = deduction (INR)
    service = Column(Text)  # e.g. "claude_sonnet", "sarvam_tts" — null for top-ups
    raw_cost = Column(Numeric)  # actual provider cost before the markup multiplier
    student_id = Column(Integer, ForeignKey("students.id"))
    note = Column(Text)
    # A Razorpay payment_id (or other external transaction id) — set only
    # for real payments, unique so the same payment can never be credited
    # twice (e.g. a client retrying /pay/verify after a slow response, or a
    # replayed request with a still-valid signature).
    external_ref = Column(Text, unique=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class SchoolCreditEvent(Base):
    """
    A separate ledger from CreditEvent (which is per-student) — teacher-
    facing tools like the workbook/practice-set PDF generator and Gamma
    presentations are billed to the SCHOOL, not any one student's wallet.
    """
    __tablename__ = "school_credit_events"

    id = Column(Integer, primary_key=True)
    amount = Column(Numeric, nullable=False)
    service = Column(Text)  # e.g. "workbook_pdf", "gamma_presentation" — null for top-ups
    raw_cost = Column(Numeric)
    centre_id = Column(Integer, ForeignKey("centres.id"), nullable=False)
    note = Column(Text)
    # Same idempotency purpose as CreditEvent.external_ref — a Razorpay
    # payment_id, unique so the same payment can never be credited twice.
    external_ref = Column(Text, unique=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class SchoolPilotGrant(Base):
    __tablename__ = "school_pilot_grants"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_school_pilot_grant_amount"),)

    id = Column(Integer, primary_key=True)
    centre_id = Column(Integer, ForeignKey("centres.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    pilot_started_at = Column(TIMESTAMP(timezone=True), nullable=False)
    amount = Column(Numeric, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
