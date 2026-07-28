from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """
    Every specialised agent (Tutor, Quiz, Homework, Parent, Teacher, ...)
    inherits from this. Keeps prompts, tools, and context-building
    consistent instead of one giant prompt (Step 8).
    """

    name: str = "base"

    @abstractmethod
    def build_context(
        self,
        student: dict,
        retrieved_chunks: list[str],
        weak_topics: list[str],
        image_generation_enabled: bool = False,
        voice_enabled: bool = False,
    ) -> str:
        """Assemble the system context: student profile + RAG chunks + known weak topics."""
        raise NotImplementedError

    @abstractmethod
    async def respond(
        self,
        student: dict,
        message: str,
        history: list[dict] | None = None,
        weak_topics: list[str] | None = None,
        image_generation_enabled: bool = False,
        voice_enabled: bool = False,
    ) -> dict:
        """
        Run retrieval (if needed) then call the LLM and return a reply, plus
        any topic-progress signal, target language, image request,
        selective-audio-reply request, and off-level-class signal extracted
        from it:
        {"reply": str, "topic": str | None, "evaluated": bool, "correct": bool | None,
         "lang": str, "image_prompt": str | None, "wants_audio_reply": bool,
         "off_level_class": str | None}
        """
        raise NotImplementedError
