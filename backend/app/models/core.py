from sqlalchemy import (
    Column, Integer, Text, Boolean, Numeric, Date, JSON, CheckConstraint, ForeignKey, TIMESTAMP, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database import Base

# JSONB in production (Postgres); falls back to generic JSON for other dialects (e.g. sqlite in tests)
JSONType = JSONB().with_variant(JSON(), "sqlite")


class Centre(Base):
    __tablename__ = "centres"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    city = Column(Text)
    # Shown on the school's own portal header and stamped onto anything
    # generated on their behalf (e.g. practice-set PDFs) alongside the
    # "Powered by Qlass Learning" mark.
    logo_url = Column(Text)
    # Lightweight sales-pipeline tracking — "prospect" (being sold to, no
    # real usage yet) | "trial" | "active" | "churned". Self-registered
    # schools (see /auth/register-school) start as "active" since they've
    # already begun using the product; Qlass-side pipeline entries created
    # ahead of an actual signup would start as "prospect".
    sales_status = Column(Text, default="active")
    sales_notes = Column(Text)
    # Free-text record of a negotiated contract (e.g. "₹50,000/year
    # unlimited, signed 2026-04-01") — not automated billing, just so a
    # custom deal is represented somewhere instead of purely in someone's
    # inbox.
    contract_notes = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    students = relationship("Student", back_populates="centre")
    teachers = relationship("Teacher", back_populates="centre")


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    # Not unique — a shared family phone can have more than one student
    # profile; see app.services.active_profile for how the active one is
    # resolved per incoming message.
    phone = Column(Text, nullable=False, index=True)
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
    # plan; a real student's is the ₹1800/year plan — same two columns
    # serve both since the mechanism (bypass the wallet check while active)
    # is identical, just the price/duration differ.
    subscription_plan = Column(Text, default="credits")
    subscription_expires_at = Column(TIMESTAMP(timezone=True))
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
    password_hash = Column(Text)
    # "teacher" | "admin" | "super_admin". "teacher"/"admin" are scoped to
    # their own centre_id (school) — this product is sold to multiple
    # schools, so each school's students/teachers must stay isolated from
    # every other school's. "admin" additionally manages other teacher
    # accounts for their own school. "super_admin" is Qlass's own staff
    # role and sees/manages across every school, centre_id is null for it.
    role = Column(Text, default="teacher")
    photo_url = Column(Text)

    centre = relationship("Centre", back_populates="teachers")


class Subject(Base):
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    class_ = Column("class", Text)

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
    embedding_id = Column(Text)  # pointer into the vector store (Chroma)

    document = relationship("Document", back_populates="chunks")


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


class Homework(Base):
    __tablename__ = "homework"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    chapter_id = Column(Integer, ForeignKey("chapters.id"))
    assigned_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    due_at = Column(TIMESTAMP(timezone=True))

    submissions = relationship("HomeworkSubmission", back_populates="homework")


class HomeworkSubmission(Base):
    __tablename__ = "homework_submission"

    id = Column(Integer, primary_key=True)
    homework_id = Column(Integer, ForeignKey("homework.id"))
    student_id = Column(Integer, ForeignKey("students.id"))
    file_url = Column(Text)
    ocr_text = Column(Text)
    marks_awarded = Column(Numeric)
    feedback = Column(Text)
    submitted_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    homework = relationship("Homework", back_populates="submissions")


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
