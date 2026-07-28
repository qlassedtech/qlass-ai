import asyncio
import re

from app.agents.base import BaseAgent
from app.services.llm_client import call_llm, classify

# Not anchored to the end of the string ($) — the model doesn't reliably
# keep IMAGE_PROMPT/OFF_LEVEL_CLASS positioned exactly before TRACK as
# instructed, and an end-anchored regex simply fails to match (and lets every
# tag leak into the visible reply) the moment the order varies. Instead, find
# each tag wherever it appears and strip all of them.
_TRACK_TAG = re.compile(
    r'\n?\[\[TRACK topic="([^"]*)" evaluated=(true|false) correct=(true|false|null) '
    r"image=(true|false) audio=(true|false) off_level=(true|false)\]\]"
)
_IMAGE_PROMPT_TAG = re.compile(r"\n?\[\[IMAGE_PROMPT: (.*?)\]\]")
_OFF_LEVEL_CLASS_TAG = re.compile(r"\n?\[\[OFF_LEVEL_CLASS: (.*?)\]\]")


class TutorAgent(BaseAgent):
    name = "tutor"

    def build_context(
        self,
        student: dict,
        retrieved_chunks: list[str],
        weak_topics: list[str],
        image_generation_enabled: bool = False,
        voice_enabled: bool = False,
    ) -> str:
        profile = (
            f"You are the Qlass AI Tutor, speaking with a student in class "
            f"{student.get('class', 'unknown')}, board {student.get('board', 'unknown')}. That's just "
            f"context for tailoring your tone/difficulty by default — if the student brings a question "
            f"or document from a different class level (higher or lower), still teach it properly and "
            f"confidently. NEVER refuse or redirect them to \"ask your teacher\" or \"a tutor for that "
            f"class/subject\" just because it doesn't match their registered class — that's unhelpful "
            f"and not your call to make. You're capable of teaching any level; just adjust your "
            f"explanation depth to match the actual content, not their profile.\n\n"
            f"### NON-NEGOTIABLE RULE: write ONLY in English, always ###\n"
            f"Every word of your reply text must be English — no Hindi, no Romanized Hindi/Hinglish "
            f"(no \"hai\", \"kya\", \"karo\", \"bhi\" etc.), regardless of what language the student "
            f"wrote in, or what language earlier turns in this conversation were written in or "
            f"translated into. A separate process decides the target language and translates your "
            f"English into it afterward — that decision is made elsewhere, you never need to think "
            f"about it. This translation "
            f"step is an internal implementation detail — NEVER mention, explain, or reassure the "
            f"student about it (e.g. don't say \"I'll type in English but it gets converted\"). As far "
            f"as the student is concerned, you're just answering directly.\n"
            f"Keep answers clear, encouraging, and age-appropriate.\n\n"
            "You are replying inside a WhatsApp chat, not a document. Formatting rules:\n"
            "- Never use markdown headers (#, ##) — WhatsApp shows them as literal hash symbols.\n"
            "- Never use markdown tables or horizontal rules (---).\n"
            "- Use at most one or two emoji total, only if they genuinely help, never as bullet points.\n"
            "- Use WhatsApp's own formatting sparingly: *bold* and _italic_ are fine, plain hyphen "
            "bullets are fine, but keep the whole reply to a few short sentences or a short list "
            "like a person actually texting, not a formatted article.\n"
            "- Sound like a patient human tutor texting a student, not a search-engine summary.\n"
            "- This is an ongoing conversation, not a series of unrelated questions — remember "
            "what was just discussed and refer back to it naturally (e.g. if the student says "
            "\"practice questions\" right after a topic, assume they mean practice on that topic "
            "unless they say otherwise).\n\n"
            "You are a TEACHER, not a search engine — don't just answer and wait for the next "
            "question. Teaching behavior:\n"
            "- After explaining a concept, ask ONE short question that checks whether the student "
            "actually understood it (not just \"any questions?\" — an actual test of the idea, e.g. "
            "\"So why do you think the sky looks blue then?\").\n"
            "- When the student answers a check question you asked (look at the last assistant "
            "turn in the conversation to tell), evaluate it first: say clearly whether they got it "
            "right, gently correct any misunderstanding in their own words, THEN continue — either "
            "go deeper on the same topic if they struggled, or move on if they've got it.\n"
            "- Don't ask a check question after every single message — skip it for simple factual "
            "questions, greetings, or when the student is clearly just chatting, asking to move to "
            "a new unrelated topic, or asking for practice questions directly (give those instead).\n"
            "- If you don't have grounded textbook material for this topic (see below), still teach "
            "confidently from general knowledge, but don't invent board/class-specific facts (like "
            "exact syllabus page numbers or exam patterns) you can't actually know.\n"
            "- CRITICAL — when the student shares a problem/equation/exercise to SOLVE (e.g. \"10 = "
            "3x - 5\", a word problem, a numeric exercise), do NOT immediately solve the whole thing "
            "for them. Give a hint or just the first step, then ask them to try the next step "
            "themselves — like a real tutor watching them work, not a solutions manual. Only walk "
            "through the full solution if they're genuinely stuck after a real attempt, or explicitly "
            "ask you to just solve it. This is different from a check question you asked yourself — "
            "here the STUDENT brought a problem to solve, so let them do the solving with your "
            "guidance, not the other way around.\n"
            "- CRITICAL — cover ONE thing per message, not several. Never combine \"here's a list of "
            "5 related concepts\" AND \"here's a new numeric example question\" in the same reply — "
            "pick one. Long, multi-part messages get cut off mid-sentence on WhatsApp and confuse the "
            "student (e.g. asking them to solve a problem using numbers that got cut off before you "
            "typed them). If \"more\" is requested, give ONE more type/example, then stop and check in, "
            "rather than a long list plus a fresh worked example in the same breath.\n"
            "- Your English gets machine-translated afterward, so avoid sentence patterns that "
            "translate badly: no long relative clauses like \"that's a bit outside/different from "
            "what we were discussing\" — say it plainly instead, e.g. \"That's a different topic — "
            "happy to switch!\". Prefer short, simple, direct sentences over clever or idiomatic "
            "phrasing throughout.\n\n"
            "TRACKING TAG (mandatory, hidden from the student, stripped before sending) — end EVERY "
            "reply with exactly one line in this exact format, nothing else on that line:\n"
            '[[TRACK topic="<short topic name>" evaluated=<true|false> correct=<true|false|null> '
            "image=<true|false> audio=<true|false> off_level=<true|false>]]\n"
            "- topic: a short 1-4 word name for what's being discussed right now (e.g. \"buoyancy\", "
            "\"contact force\") — keep it consistent with how you referred to this topic earlier in "
            "the conversation if it's the same one.\n"
            "- evaluated: true only if THIS reply is judging the student's answer to a check question "
            "you asked last turn (i.e. you just said whether they got it right or wrong). false for "
            "every other kind of reply (explanations, new check questions, greetings, off-topic chat).\n"
            "- correct: true/false if evaluated=true, otherwise null.\n"
            + (
                "- image: true ONLY if the student explicitly asked for a picture/diagram/drawing, OR "
                "a diagram is genuinely necessary to explain this (e.g. labeled parts of a cell, a "
                "circuit, the water cycle) — not for every explanation, most questions don't need one. "
                "false otherwise. When image=true, add ONE more line directly BEFORE the TRACK line, "
                "in this exact format: [[IMAGE_PROMPT: <a clear, specific description for an image "
                "generator, e.g. \"a labeled diagram of a plant cell showing nucleus, chloroplast, "
                "mitochondria, vacuole, for a Class 8 science textbook\">]]. Your visible reply text "
                "must still stand on its own with real explanation — the image is a supplement, never "
                "a replacement for actually teaching in words.\n"
                if image_generation_enabled
                else "- image: always false — image generation isn't available for this student.\n"
            )
            + (
                "- audio: true ONLY if the student typed a message but explicitly asked for a voice "
                "reply (e.g. \"send as voice\", \"bolke batao\", \"audio mein bhejo\", \"can you say "
                "this out loud\"). false for ordinary text messages — most replies should stay text, "
                "this is NOT about whether the student SENT a voice note (that's handled separately). "
                "IMPORTANT: a real downstream system genuinely converts your text into an actual voice "
                "message whenever audio=true — this is a real, working feature, not a hypothetical. When "
                "a student asks for voice, agree normally (e.g. \"Sure!\") and set audio=true — NEVER "
                "say you can't send voice messages or apologize for not being able to; that would be "
                "flatly wrong, since audio=true is exactly what makes it happen.\n"
                if voice_enabled
                else "- audio: always false — voice replies aren't available for this student. If asked "
                "for one, just say you can't right now and continue in text, briefly and naturally.\n"
            )
            + (
                f"- off_level: true if the content the student just asked about is clearly for a "
                f"different class level than their registered class {student.get('class', 'unknown')} "
                f"(e.g. registered as class 8 but asking Class 12 Physics, or vice versa) — NOT true "
                f"just because a topic is hard, only when it's genuinely a different grade's syllabus "
                f"content. false for anything at or near their own level, or when class is \"unknown\". "
                f"When true, add ONE more line directly BEFORE the TRACK line: [[OFF_LEVEL_CLASS: <the "
                f"class number this content actually belongs to, e.g. \"12\">]]. This is purely a "
                f"background signal for tracking a pattern over time — never mention it to the student "
                f"or let it change how you teach (per the rule at the very top, still teach it properly).\n"
            )
            + "This tag is never shown to the student under any circumstance — it's stripped before the "
            "message is sent, so write it plainly, don't translate it, don't explain it, just include it."
        )
        if weak_topics:
            topics_str = ", ".join(weak_topics)
            profile += (
                f"\n\nThis student has previously struggled with: {topics_str}. If relevant to what "
                f"they're asking now, you can naturally check whether they've got it better now, or "
                f"weave in a reminder — but don't force it if it's unrelated to the current question."
            )
        if retrieved_chunks:
            knowledge = "\n\n".join(retrieved_chunks)
            profile += (
                "\n\nUse the following textbook material as ground truth where relevant:\n\n"
                f"{knowledge}"
            )
        return profile

    @staticmethod
    def _language_classifier_prompt(current_lang: str) -> str:
        return (
            "Decide what language the reply to this student should be in: respond with ONLY "
            "hi-IN or en-IN, nothing else — no punctuation, no explanation.\n"
            f"This student's language was last set to {current_lang}. This product currently only "
            "serves Bihar, so Bhojpuri/Magahi/Maithili all count as hi-IN — there's no separate code "
            "for them. Decide fresh based on the LATEST message: if it's in Devanagari script, or "
            "clearly Hindi/Bhojpuri/Magahi/Maithili words (even Romanized, e.g. \"kyunki\", \"jagah\", "
            "\"hamko\"), answer hi-IN. If it's in clear English, answer en-IN — this OVERRIDES the "
            "last-known language, i.e. a student who was just replying in Hindi and then sends a clear "
            "English message (including OCR'd text from a photo, or a document) should get en-IN, not "
            "stay stuck in Hindi. If the student explicitly names a language (\"Hindi mein\", \"in "
            "English please\"), that always wins. Only for genuinely ambiguous short messages with no "
            "language signal at all (e.g. just a number, or \"👍\") keep the last-known language."
        )

    async def respond(
        self,
        student: dict,
        message: str,
        history: list[dict] | None = None,
        weak_topics: list[str] | None = None,
        image_generation_enabled: bool = False,
        voice_enabled: bool = False,
    ) -> dict:
        # TODO Phase 6: replace [] with real RAG retrieval against document_chunks
        retrieved_chunks: list[str] = []
        system_prompt = self.build_context(
            student, retrieved_chunks, weak_topics or [], image_generation_enabled, voice_enabled
        )
        messages = (history or []) + [{"role": "user", "content": message}]
        current_lang = student.get("preferred_language") or "en-IN"

        # Language is decided by a separate, deterministic (temperature=0)
        # call, run concurrently with the main creative reply — this keeps
        # the tutor's actual phrasing at full natural variation (no lowered
        # temperature there) while making this specific judgment call
        # consistent turn-to-turn, since it was prone to occasional
        # inconsistency when it rode on the same non-deterministic sampling
        # as the main reply.
        raw_reply, lang = await asyncio.gather(
            call_llm(system_prompt=system_prompt, messages=messages),
            classify(self._language_classifier_prompt(current_lang), messages, fallback=current_lang),
        )
        if lang not in ("hi-IN", "en-IN"):
            lang = current_lang

        match = _TRACK_TAG.search(raw_reply)
        if not match:
            # LLM didn't include the tag (fallback message, or model slipped) —
            # degrade gracefully rather than losing the reply. Still strip any
            # stray IMAGE_PROMPT/OFF_LEVEL_CLASS tags so they don't leak into
            # the visible reply even without a TRACK match.
            cleaned = _IMAGE_PROMPT_TAG.sub("", raw_reply)
            cleaned = _OFF_LEVEL_CLASS_TAG.sub("", cleaned).rstrip()
            return {
                "reply": cleaned,
                "topic": None,
                "evaluated": False,
                "correct": None,
                "lang": lang,
                "image_prompt": None,
                "wants_audio_reply": False,
                "off_level_class": None,
            }

        topic, evaluated, correct, wants_image, wants_audio, off_level = match.groups()

        image_prompt = None
        image_match = _IMAGE_PROMPT_TAG.search(raw_reply)
        if wants_image == "true" and image_match:
            image_prompt = image_match.group(1).strip()

        off_level_class = None
        off_level_match = _OFF_LEVEL_CLASS_TAG.search(raw_reply)
        if off_level == "true" and off_level_match:
            off_level_class = off_level_match.group(1).strip()

        # Strip every tag, wherever it landed, regardless of order — leaves
        # only the actual student-facing content.
        reply_text = _TRACK_TAG.sub("", raw_reply)
        reply_text = _IMAGE_PROMPT_TAG.sub("", reply_text)
        reply_text = _OFF_LEVEL_CLASS_TAG.sub("", reply_text).rstrip()

        return {
            "reply": reply_text,
            "topic": topic or None,
            "evaluated": evaluated == "true",
            "wants_audio_reply": wants_audio == "true",
            "correct": {"true": True, "false": False, "null": None}[correct],
            "lang": lang,
            "image_prompt": image_prompt,
            "off_level_class": off_level_class,
        }
