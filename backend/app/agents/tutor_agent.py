from app.agents.base import BaseAgent
from app.services.llm_client import call_llm


class TutorAgent(BaseAgent):
    name = "tutor"

    def build_context(self, student: dict, retrieved_chunks: list[str]) -> str:
        profile = (
            f"You are the Qlass AI Tutor, speaking with a student in class "
            f"{student.get('class', 'unknown')}, board {student.get('board', 'unknown')}. "
            f"Reply in {student.get('preferred_language', 'en')}. "
            f"Keep answers clear, encouraging, and age-appropriate."
        )
        if retrieved_chunks:
            knowledge = "\n\n".join(retrieved_chunks)
            profile += (
                "\n\nUse the following textbook material as ground truth where relevant:\n\n"
                f"{knowledge}"
            )
        return profile

    async def respond(self, student: dict, message: str) -> str:
        # TODO Phase 6: replace [] with real RAG retrieval against document_chunks
        retrieved_chunks: list[str] = []
        system_prompt = self.build_context(student, retrieved_chunks)
        return call_llm(system_prompt=system_prompt, user_message=message)
