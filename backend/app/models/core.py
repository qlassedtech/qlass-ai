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
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
