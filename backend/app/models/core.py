from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Numeric, ForeignKey, TIMESTAMP, func
)
from sqlalchemy.orm import relationship

from app.database import Base


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    phone = Column(Text, unique=True, nullable=False)
    class_ = Column("class", Text)
    board = Column(Text)
    school = Column(Text)
    preferred_language = Column(Text, default="en")
    centre_id = Column(Integer, ForeignKey("centres.id"))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    chat_history = relationship("ChatHistory", back_populates="student")

    def as_profile_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "class": self.class_,
            "board": self.board,
            "school": self.school,
            "preferred_language": self.preferred_language,
        }


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    role = Column(Text)  # 'user' | 'assistant'
    message = Column(Text)
    agent = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    student = relationship("Student", back_populates="chat_history")


class Centre(Base):
    __tablename__ = "centres"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    city = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
