from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """
    Every specialised agent (Tutor, Quiz, Homework, Parent, Teacher, ...)
    inherits from this. Keeps prompts, tools, and context-building
    consistent instead of one giant prompt (Step 8).
    """

    name: str = "base"

    @abstractmethod
    def build_context(self, student: dict, retrieved_chunks: list[str]) -> str:
        """Assemble the system context: student profile + RAG chunks."""
        raise NotImplementedError

    @abstractmethod
    async def respond(self, student: dict, message: str) -> str:
        """Run retrieval (if needed) then call the LLM and return a reply."""
        raise NotImplementedError
