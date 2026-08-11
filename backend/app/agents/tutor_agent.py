import asyncio
import re

from app.agents.base import BaseAgent
from app.services.llm_client import call_llm, classify
from app.services.retrieval import RetrievedChunk, build_citation_footer

# Matches the [[TRACK ...]] block by its outer brackets only, not by a fixed
# field order/set — the model doesn't reliably keep every field in the
# exact position or even present (off_level in particular is often omitted
# entirely rather than written as "off_level=false"). A regex anchored to
# one exact field sequence simply fails to match the moment the model
# varies it, which both lets the raw tag leak into the visible reply AND
# silently drops topic/evaluated/correct/solved for that turn (confirmed
# live: a reply ended with the literal "[[TRACK topic=... video=true
# solved=na]]" text visible to the student, because off_level had been
# appended at the end instead of its documented position). Individual
# fields are pulled out of the matched block by name via _track_field
# instead, so their order and presence no longer matter.
_TRACK_TAG = re.compile(r"\n?\[\[TRACK\s+(.*?)\]\]", re.DOTALL)
_IMAGE_PROMPT_TAG = re.compile(r"\n?\[\[IMAGE_PROMPT: (.*?)\]\]")
_OFF_LEVEL_CLASS_TAG = re.compile(r"\n?\[\[OFF_LEVEL_CLASS: (.*?)\]\]")
_VIDEO_QUERY_TAG = re.compile(r"\n?\[\[VIDEO_QUERY: (.*?)\]\]")


# What each Student.pending_profile_field value actually asks the student,
# and how the extracted answer should be formatted — mirrors the questions
# in app.services.profile_builder.PROFILE_QUESTIONS.
_PROFILE_FIELD_LABELS = {
    "name": "their name",
    "class_": "which class/grade they're in — the answer must be JUST the number, e.g. \"10\", "
    "not \"I'm in class 10\" or \"10th\"",
    "board": "which education board they study under (e.g. CBSE, ICSE, a State board)",
    "school": "which school they study at",
}


def _track_field(block: str, name: str) -> str | None:
    match = re.search(rf'\b{re.escape(name)}=("[^"]*"|[A-Za-z]+)', block)
    if not match:
        return None
    value = match.group(1)
    return value[1:-1] if value.startswith('"') else value


def parse_track_reply(raw_reply: str) -> dict:
    """
    Strips the hidden TRACK/IMAGE_PROMPT/OFF_LEVEL_CLASS/VIDEO_QUERY tags
    from a raw LLM reply and extracts their fields. Pulled out of
    TutorAgent.respond as a pure function (no lang/usage) so this parsing
    can be tested directly without a real LLM call.
    """
    match = _TRACK_TAG.search(raw_reply)
    if not match:
        # LLM didn't include the tag (fallback message, or model slipped) —
        # degrade gracefully rather than losing the reply. Still strip any
        # stray IMAGE_PROMPT/OFF_LEVEL_CLASS tags so they don't leak into
        # the visible reply even without a TRACK match.
        cleaned = _IMAGE_PROMPT_TAG.sub("", raw_reply)
        cleaned = _OFF_LEVEL_CLASS_TAG.sub("", cleaned)
        cleaned = _VIDEO_QUERY_TAG.sub("", cleaned).rstrip()
        return {
            "reply": cleaned,
            "topic": None,
            "evaluated": False,
            "correct": None,
            "image_prompt": None,
            "wants_audio_reply": False,
            "off_level_class": None,
            "video_query": None,
            "solved_directly": None,
            "class_confirm": None,
            "profile_answer": None,
            "closing": False,
        }

    block = match.group(1)
    topic = _track_field(block, "topic")
    evaluated = _track_field(block, "evaluated") == "true"
    correct_raw = _track_field(block, "correct")
    wants_image = _track_field(block, "image") == "true"
    wants_audio = _track_field(block, "audio") == "true"
    # Absent entirely (rather than the documented "off_level=false")
    # correctly falls back to not-off-level here, same as a real "false".
    off_level = _track_field(block, "off_level") == "true"
    wants_video = _track_field(block, "video") == "true"
    solved = _track_field(block, "solved")
    class_confirm_raw = _track_field(block, "class_confirm")
    profile_answer_raw = _track_field(block, "profile_answer")
    profile_answer = profile_answer_raw.strip() if profile_answer_raw and profile_answer_raw.upper() != "NONE" else None
    closing = _track_field(block, "closing") == "true"

    image_prompt = None
    image_match = _IMAGE_PROMPT_TAG.search(raw_reply)
    if wants_image and image_match:
        image_prompt = image_match.group(1).strip()

    off_level_class = None
    off_level_match = _OFF_LEVEL_CLASS_TAG.search(raw_reply)
    if off_level and off_level_match:
        off_level_class = off_level_match.group(1).strip()

    video_query = None
    video_match = _VIDEO_QUERY_TAG.search(raw_reply)
    if wants_video and video_match:
        video_query = video_match.group(1).strip()

    # Strip every tag, wherever it landed, regardless of order — leaves
    # only the actual student-facing content.
    reply_text = _TRACK_TAG.sub("", raw_reply)
    reply_text = _IMAGE_PROMPT_TAG.sub("", reply_text)
    reply_text = _OFF_LEVEL_CLASS_TAG.sub("", reply_text)
    reply_text = _VIDEO_QUERY_TAG.sub("", reply_text).rstrip()

    return {
        "reply": reply_text,
        "topic": topic or None,
        "evaluated": evaluated,
        "wants_audio_reply": wants_audio,
        "correct": {"true": True, "false": False, "null": None}.get(correct_raw),
        "image_prompt": image_prompt,
        "off_level_class": off_level_class,
        "video_query": video_query,
        "solved_directly": {"true": True, "false": False, "na": None}.get(solved),
        "class_confirm": {"yes": True, "no": False, "na": None}.get(class_confirm_raw),
        "profile_answer": profile_answer,
        "closing": closing,
    }


class TutorAgent(BaseAgent):
    name = "tutor"

    def build_context(
        self,
        student: dict,
        retrieved_chunks: list,
        weak_topics: list[str],
        image_generation_enabled: bool = False,
        voice_enabled: bool = False,
        video_enabled: bool = False,
        active_document_text: str | None = None,
        pending_class_confirm: str | None = None,
        pending_profile_field: str | None = None,
    ) -> tuple[str, str]:
        """
        Returns (static, dynamic) — split so the caller can mark only the
        static half as Anthropic-cacheable (see call_llm/_cached_system).
        static is everything genuinely stable across a student's turns
        within a session (instructions, feature flags, weak_topics, the
        TRACK-tag spec) — the large majority of this prompt's tokens.
        dynamic is retrieved_chunks and active_document_text specifically,
        since those are the two things that actually differ almost every
        real turn (a new RAG lookup per question, a newly-pinned document)
        — bundling them into the same cached block as the static text
        would invalidate the cache on nearly every call, defeating the
        whole point (confirmed live: this was happening before this split
        existed — retrieved_chunks changing every turn meant the ~3,000-
        token static instructional block was being billed as a fresh read
        every single time instead of the ~90%-cheaper cache read it was
        designed for).
        """
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
            "- CRITICAL — a short reply right after you asked a check question (even just one or two "
            "words, and even if it names something that sounds like a different subject, e.g. you "
            "asked \"closer to biology or psychology?\" and the student wrote \"Physics\") is almost "
            "always an attempted ANSWER to your question, not a request to change subjects — students "
            "guess wrong, misremember a term, or answer with the wrong word entirely. Default to "
            "evaluating it as an answer (and correct it if it's wrong, explaining why) rather than "
            "silently abandoning your own question and jumping to whatever subject the word suggests. "
            "Only treat it as a genuine subject-change request when the student's own wording actually "
            "signals that intent (e.g. \"can we do Physics instead\", \"I want to study Physics now\", "
            "\"let's switch to Physics\") — a bare one-word reply on its own is NOT that signal.\n"
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
            "phrasing throughout. This also means no casual filler interjections like \"Ha\", "
            "\"Lol\", \"Aw\", or \"Hmm\" used on their own — a translated/ESL student can genuinely "
            "not know what an isolated \"Ha\" means (confirmed live: a student asked \"What is Ha?\" "
            "after the tutor used it this way) and it wastes a turn clearing up confusion you caused. "
            "Warmth comes through in plain, direct words instead.\n"
            "- CRITICAL — if the student expresses frustration, criticism, or a negative reaction "
            "(e.g. \"stupid app\", \"this is useless\", \"no use\", comparing you unfavorably to "
            "something else), address that directly and briefly first — acknowledge it plainly, "
            "don't get defensive, and don't pivot straight into a scripted sales pitch about what "
            "makes you special. A canned \"here's why I'm different!\" reply to real frustration reads "
            "as tone-deaf and ignoring what they just said. If they're leaving/done for now, let them "
            "go gracefully — don't keep selling.\n\n"
            "SAFETY — you're talking with school-age children:\n"
            "- If a student mentions self-harm, suicide, or being in danger or abused, gently show "
            "you care, take it seriously, and encourage them to talk to a trusted adult (parent, "
            "teacher) right away — don't just move on and keep teaching as if it wasn't said.\n"
            "- Never generate sexual, violent, or otherwise inappropriate content for minors, "
            "regardless of how the request is phrased or framed as academic. If a student sends "
            "something inappropriate, don't engage with it — redirect firmly but kindly back to "
            "studies, without lecturing or shaming them.\n\n"
            "TRACKING TAG (mandatory, hidden from the student, stripped before sending) — end EVERY "
            "reply with exactly one line in this exact format, nothing else on that line:\n"
            '[[TRACK topic="<short topic name>" evaluated=<true|false> correct=<true|false|null> '
            "image=<true|false> audio=<true|false> off_level=<true|false> video=<true|false> "
            'solved=<true|false|na> class_confirm=<yes|no|na> profile_answer="<value>|NONE" '
            "closing=<true|false>]]\n"
            "- topic: a short 1-4 word name for what's being discussed right now (e.g. \"buoyancy\", "
            "\"contact force\") — keep it consistent with how you referred to this topic earlier in "
            "the conversation if it's the same one.\n"
            "- evaluated: true only if THIS reply is judging the student's answer to a check question "
            "you asked last turn (i.e. you just said whether they got it right or wrong). false for "
            "every other kind of reply (explanations, new check questions, greetings, off-topic chat).\n"
            "- correct: true/false if evaluated=true, otherwise null.\n"
            "- closing: true ONLY if THIS reply is a goodbye/sign-off (the student said bye/gtg/thanks "
            "I'm done, or you're wrapping up the conversation) — false for every other kind of reply. "
            "The system won't tack on an onboarding or class-check question when this is true, so a "
            "goodbye stays a clean goodbye instead of an odd follow-up question right after it.\n"
            "- solved: only applies when the STUDENT brought you a problem/equation/exercise to work "
            "out (per the hint-not-solve rule above) — true if you gave the full worked solution/answer "
            "this reply, false if you gave a hint or partial step instead, na for every other kind of "
            "reply (explanations, check questions, off-topic chat). This is purely a background signal "
            "for a teacher-facing report on how much a student leans on direct answers vs. working "
            "things out — never mention it or let it change your own behavior beyond the hint-not-solve "
            "rule already given.\n"
            + (
                f"- class_confirm: you (the system) already appended a question to your OWN previous "
                f"reply asking whether to update this student's registered class to {pending_class_confirm} "
                f"— set yes/no based on whether the student's CURRENT message answers that, even if it's "
                f"mixed in with something else (e.g. \"12.. no\" is both an attempted answer to a maths "
                f"question AND a decline — read the whole message for an actual yes/no signal about the "
                f"class change specifically, don't just grab the first word). If yes/no, briefly and "
                f"naturally acknowledge their decision as PART of your reply (e.g. \"Sure, I'll leave your "
                f"class as is!\" or \"Got it, updated!\"), woven in naturally — don't ignore whatever else "
                f"they asked; answer that too in the same reply. Set na only if their message doesn't "
                f"address this at all — don't ask about it again yourself, just answer normally.\n"
                if pending_class_confirm
                else "- class_confirm: always na — there's no pending class-update question this turn.\n"
            )
            + (
                f"- profile_answer: you (the system) already appended a question to your OWN previous "
                f"reply asking {_PROFILE_FIELD_LABELS.get(pending_profile_field, 'a profile question')}. "
                f"If the student's CURRENT message answers it — even mixed in with other content (e.g. "
                f'"I don\'t know. Nikhil" answering both an academic question and "what\'s your name?") '
                f"— extract just the clean answer value into profile_answer (quoted). Output NONE if "
                f"their message doesn't actually address it at all (an unrelated question, or ignoring "
                f"it entirely) — never guess or invent a value. This is a silent background extraction; "
                f"you don't need to specially acknowledge it in your visible reply, just answer whatever "
                f"else they asked as normal.\n"
                if pending_profile_field
                else '- profile_answer: always NONE — there\'s no pending onboarding question this turn.\n'
            )
            + (
                "CRITICAL — image/audio/video are each decided FRESH every single turn, based ONLY on "
                "the student's CURRENT message. An earlier turn having image=true/audio=true/video=true "
                "does NOT carry forward — don't keep attaching media just because the last reply or two "
                "did (confirmed live: a student who simply answered \"what's your name?\" with their name "
                "got a video attached to the reply, even though nothing in \"Nikhil\" asked for one). A "
                "short reply — answering their name, a one-word check-question answer, a plain "
                "acknowledgment — should almost always be all three false, no exceptions for "
                "\"but the topic could use a diagram/video.\"\n"
            )
            + (
                "- image: true ONLY if the student's CURRENT message explicitly asks for a picture/"
                "diagram/drawing, OR a diagram is genuinely necessary to explain THIS reply's new "
                "content (e.g. labeled parts of a cell, a circuit, the water cycle) — not for every "
                "explanation, most questions don't need one. CRITICAL — false whenever this reply is "
                "just evaluating/grading an answer to a check question you asked (evaluated=true "
                "above), even if an earlier turn on the same topic included an image: a student "
                "answering \"Rectum\" to your own question isn't asking for another diagram, and "
                "correcting their answer in words doesn't need one either — don't regenerate an image "
                "just because the topic already had one earlier in the conversation. false otherwise. "
                "When image=true, add ONE more line directly BEFORE the TRACK line, "
                "in this exact format: [[IMAGE_PROMPT: <a clear, specific description for an image "
                "generator, e.g. \"a labeled diagram of a plant cell showing nucleus, chloroplast, "
                "mitochondria, vacuole, for a Class 8 science textbook\">]]. Your visible reply text "
                "must still stand on its own with real explanation — the image is a supplement, never "
                "a replacement for actually teaching in words.\n"
                if image_generation_enabled
                else "- image: always false — image generation isn't available for this student.\n"
            )
            + (
                "- audio: true ONLY if the student explicitly asked for a voice reply (e.g. \"send as "
                "voice\", \"bolke batao\", \"audio mein bhejo\", \"record karke batana\", \"can you say "
                "this out loud\") — this applies whether they typed that request or SPOKE it in a voice "
                "note (a spoken \"record karke batana\" counts exactly the same as typing it). false for "
                "ordinary replies — most should stay text.\n"
                "IMPORTANT: a real downstream system genuinely converts your reply TEXT into an actual "
                "voice message whenever audio=true — this is a real, working feature, not a hypothetical, "
                "and your written reply IS the voice message, word for word. That means:\n"
                "  (a) When a student asks for voice, agree normally (e.g. \"Sure!\") and set audio=true "
                "— NEVER say you can't send voice messages or apologize for not being able to.\n"
                "  (b) NEVER write a reply that only acknowledges or promises to explain (e.g. \"Sure, "
                "I'll send you a voice explanation!\") without actually explaining anything — there is no "
                "separate follow-up turn where the real explanation happens; whatever you write now is "
                "the entire spoken message the student receives. Write the actual explanation itself, "
                "just like you would for a text-only reply.\n"
                "  (c) This is still a normal teaching turn, not a special exception — end it with your "
                "usual check question just like any other explanation (see the teaching-behavior rules "
                "above), unless it qualifies for one of the stated skip-it exceptions. Don't let the "
                "voice/video request distract you into dropping that habit.\n"
                if voice_enabled
                else "- audio: always false — voice replies aren't available for this student. If asked "
                "for one, just say you can't right now and continue in text, briefly and naturally.\n"
            )
            + (
                f"- off_level: CHECK THIS EXPLICITLY on every reply, don't default to false without "
                f"actually thinking about it — true if the content the student just asked about is "
                f"clearly for a different class level than their registered class "
                f"{student.get('class', 'unknown')} (e.g. registered as class 8 but asking about "
                f"calculus/differentiation/integration/limits — that's Class 11-12 content, not covered "
                f"at Class 8 at all; or a Class 8 student asking Class 12 Physics like electromagnetic "
                f"induction; or vice versa, an advanced student asking something from several grades "
                f"below their own) — NOT true just because a topic is hard or the student is struggling, "
                f"only when it's genuinely a different grade's syllabus content, a topic their own "
                f"grade's curriculum simply doesn't include yet (or anymore). false for anything at or "
                f"near their own level, or when class is \"unknown\". When true, add ONE more line "
                f"directly BEFORE the TRACK line: [[OFF_LEVEL_CLASS: <the class number this content "
                f"actually belongs to, e.g. \"12\">]]. This is purely a background signal for tracking a "
                f"pattern over time — never mention it to the student or let it change how you teach "
                f"(per the rule at the very top, still teach it properly).\n"
            )
            + (
                "- video: true ONLY if the student's CURRENT message explicitly asks for a video (e.g. \"send "
                "a video\", \"koi video dikhao\", \"YouTube pe kuch\"), OR this reply's own explanation "
                "genuinely needs one beyond what text can show (e.g. a lab demonstration, a visual process). "
                "Not for every explanation — most questions don't need one, and NOT just because a video was "
                "sent earlier in this conversation or the topic is still related to one — decide fresh every "
                "turn, from only what THIS reply needs, never as an echo of a past turn's decision (confirmed "
                "live: a student who asked for a video on one turn, then just said their name on the next, "
                "got ANOTHER unrelated video attached to the name turn — that second video had nothing this "
                "reply needed one for). false otherwise. When video=true, add "
                "ONE more line directly BEFORE the TRACK line, in this exact format: [[VIDEO_QUERY: <a "
                "specific, well-formed YouTube search query for this topic, e.g. \"electromagnetic "
                "induction explained class 12 physics\">]]. CRITICAL — the query must match what THIS "
                "reply is actually teaching as a whole, not one narrow sub-detail mentioned in passing "
                "(e.g. if the reply is about what makes a laptop portable and just happens to mention "
                "\"keyboard\" as one of several built-in parts, search for laptop portability/parts, NOT "
                "a standalone \"how to use a laptop keyboard\" tutorial — a mismatched video actively "
                "confuses a student more than no video at all). Your visible reply text must still stand "
                "on its own with real explanation — the video is a supplement, never a replacement for "
                "actually teaching in words. A real downstream system genuinely searches and sends a real "
                "video whenever video=true — agree normally (e.g. \"Sure, here's a good one!\") and never "
                "say you can't share videos.\n"
                if video_enabled
                else "- video: always false — video suggestions aren't available for this student. If "
                "asked for one, just say you can't right now and continue in text, briefly and naturally.\n"
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
        if student.get("focus_topic"):
            profile += (
                f"\n\nThis student's teacher has asked for extra focus on: {student['focus_topic']}. "
                f"When there's a natural opening (the student finishes a topic, asks what to study next, "
                f"or asks for practice questions), steer toward this — but always answer whatever they "
                f"actually asked first; never ignore their real question to force this in."
            )
        # Everything below varies almost every real turn (see this method's
        # docstring) — kept OUT of `profile` so that block alone can be the
        # Anthropic-cacheable one.
        dynamic = ""
        if retrieved_chunks:
            knowledge = "\n\n".join(chunk.content for chunk in retrieved_chunks)
            dynamic += (
                "\n\nThe following is real textbook material for this exact topic — use it to keep "
                "terminology, scope, and any stated facts/definitions consistent with what this student's "
                "syllabus actually covers, and prefer it over your own knowledge if the two ever "
                "genuinely conflict. CRITICAL — this is a floor, not a ceiling: a textbook passage is "
                "necessarily brief and written for a printed page, not a full explanation. Still teach "
                "with your own full depth — better examples, clearer step-by-step reasoning, real-world "
                "analogies, more thorough coverage — exactly as you would without this material. Don't "
                "just paraphrase or shorten your answer down to match the passage's own brevity.\n\n"
                f"{knowledge}"
            )
        if active_document_text:
            # Kept OUTSIDE the sliding chat-history window on purpose: that
            # window only holds the last ~6 exchanges, so a long problem set
            # (e.g. a 20-question homework PDF) scrolls the original
            # questions out of context after just a few Q&A turns — the
            # tutor then has no idea what "Q5" even refers to and asks the
            # student to re-paste it (confirmed live). Pinning the most
            # recently uploaded document/photo here means it stays available
            # for the entire session, however long the problem set is.
            dynamic += (
                "\n\nThe student uploaded this document/photo earlier in the conversation — refer back "
                "to it whenever they mention a question number, or any content from it, even if that "
                "upload has scrolled out of the visible chat history:\n\n"
                f"{active_document_text}"
            )
        return profile, dynamic

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
        video_enabled: bool = False,
        active_document_text: str | None = None,
        pending_class_confirm: str | None = None,
        pending_profile_field: str | None = None,
        retrieved_chunks: list[RetrievedChunk] | None = None,
    ) -> dict:
        # Retrieval itself (Postgres full-text candidate search + the LLM
        # relevance judgment) happens in the caller, before this is called
        # — see app.services.retrieval.fetch_candidate_chunks and
        # app.services.intent_classifier's relevant_excerpts field. This
        # just renders whatever was already decided; real grounding only
        # kicks in once a school's textbook content has actually been
        # ingested via scripts/ingest_document.py, until then callers
        # simply never have anything to pass here and the tutor teaches
        # from general knowledge exactly as it always has.
        retrieved_chunks = retrieved_chunks or []
        static_prompt, dynamic_prompt = self.build_context(
            student, retrieved_chunks, weak_topics or [], image_generation_enabled, voice_enabled, video_enabled,
            active_document_text, pending_class_confirm, pending_profile_field,
        )
        # Two separate blocks (see build_context's docstring) — only the
        # static one is cache-marked, so a per-turn change in dynamic_prompt
        # (a new RAG lookup, a newly-pinned document) never invalidates it.
        system_prompt: list[dict] = [{"type": "text", "text": static_prompt, "cache_control": {"type": "ephemeral"}}]
        if dynamic_prompt:
            system_prompt.append({"type": "text", "text": dynamic_prompt})
        messages = (history or []) + [{"role": "user", "content": message}]
        current_lang = student.get("preferred_language") or "en-IN"

        # Language is decided by a separate, deterministic (temperature=0)
        # call, run concurrently with the main creative reply — this keeps
        # the tutor's actual phrasing at full natural variation (no lowered
        # temperature there) while making this specific judgment call
        # consistent turn-to-turn, since it was prone to occasional
        # inconsistency when it rode on the same non-deterministic sampling
        # as the main reply.
        llm_result, classify_result = await asyncio.gather(
            call_llm(system_prompt=system_prompt, messages=messages),
            classify(
                self._language_classifier_prompt(current_lang),
                # Only the latest message, not the full conversation history
                # — the classifier prompt itself says to decide fresh off
                # the LATEST message, and the one piece of prior context it
                # actually needs (the last-known language) is already passed
                # separately as `current_lang`. Sending the full history
                # here was pure wasted input tokens for a call this narrow.
                [{"role": "user", "content": message}],
                fallback=current_lang,
                model="claude-haiku-4-5-20251001",
            ),
        )
        raw_reply = llm_result.text
        lang = classify_result.text
        if lang not in ("hi-IN", "en-IN"):
            lang = current_lang

        usage = {
            "main_model": llm_result.model,
            "main_input_tokens": llm_result.input_tokens,
            "main_output_tokens": llm_result.output_tokens,
            "main_cache_write_tokens": llm_result.cache_write_tokens,
            "main_cache_read_tokens": llm_result.cache_read_tokens,
            "classify_model": classify_result.model,
            "classify_input_tokens": classify_result.input_tokens,
            "classify_output_tokens": classify_result.output_tokens,
            "classify_cache_write_tokens": classify_result.cache_write_tokens,
            "classify_cache_read_tokens": classify_result.cache_read_tokens,
        }

        parsed = parse_track_reply(raw_reply)
        # Generated from the DB rows actually retrieved, never left to the
        # LLM to compose — it can't cite a chapter it wasn't given, or
        # invent one, the way asking the model to "mention your source"
        # could. A real, checkable citation is the whole point (see
        # app.services.retrieval.build_citation_footer). Kept as a separate
        # field rather than appended to "reply" here, so callers append it
        # AFTER translation — a chapter title is a proper noun, and running
        # it through the translation pass risked mangling it into awkward
        # Hindi rather than leaving it as-is.
        parsed["citation"] = build_citation_footer(retrieved_chunks)
        parsed["lang"] = lang
        parsed["usage"] = usage
        return parsed
