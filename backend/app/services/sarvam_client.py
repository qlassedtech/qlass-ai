import asyncio
import base64
import re

import httpx
from app.config import settings
from app.services.audio_qa import has_audio_glitch

SARVAM_BASE_URL = "https://api.sarvam.ai"

_MARKDOWN_EMPHASIS = re.compile(r"[*_]")

# Launching in Bihar only — confine language handling to what's actually
# relevant there. Sarvam has no dedicated code for Bhojpuri or Magahi (not
# in its supported language set at all); since Bihar has near-universal
# Hindi literacy/comprehension and these languages share script and much
# vocabulary with Hindi, they're routed to Hindi rather than mistranslated
# into an unrelated language. This also eliminates the Punjabi-misdetection
# class of bug entirely for this market, since detection can never drift to
# an irrelevant language.
SUPPORTED_LANGUAGES = {"hi-IN", "en-IN"}

# Explicit language-name mentions (English or Romanized). Checked before
# falling back to statistical detection — a student typing "Hindi mein pls"
# is a far stronger, explicit signal than trying to guess the language of
# ambiguous Romanized text.
_EXPLICIT_LANGUAGE_NAMES = {
    "hindi": "hi-IN",
    "bhojpuri": "hi-IN",  # no dedicated Sarvam code; closest supported language
    "magahi": "hi-IN",  # no dedicated Sarvam code; closest supported language
    "maithili": "hi-IN",  # no dedicated Sarvam code; closest supported language
    "english": "en-IN",
}


def detect_explicit_language_request(text: str) -> str | None:
    """
    Catches an explicit ask like "Hindi mein pls" or "Bhojpuri mein bolo" via
    simple keyword matching — no API call needed, and more reliable than
    statistical detection for this specific case.
    """
    lowered = text.lower()
    for name, code in _EXPLICIT_LANGUAGE_NAMES.items():
        if name in lowered:
            return code
    return None


def clamp_to_supported_language(language_code: str) -> str:
    """
    Map any detected language outside the Bihar-relevant set to the closest
    supported one, rather than translating into a language irrelevant to
    this market (or, worse, one Sarvam misdetected).
    """
    if language_code in SUPPORTED_LANGUAGES:
        return language_code
    return "en-IN" if language_code.startswith("en") else "hi-IN"


async def transcribe_audio(audio_bytes: bytes, filename: str = "voice_note.ogg") -> str | None:
    """
    Speech-to-text via Sarvam's Saaras model. Returns the transcript, or
    None if Sarvam isn't configured or the call fails.
    """
    if not settings.sarvam_api_key:
        return None

    headers = {"api-subscription-key": settings.sarvam_api_key}
    files = {"file": (filename, audio_bytes)}
    data = {"model": "saaras:v3", "language_code": "unknown"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{SARVAM_BASE_URL}/speech-to-text", headers=headers, files=files, data=data
            )
            resp.raise_for_status()
            return resp.json().get("transcript")
    except httpx.HTTPError:
        return None


async def _call_tts_api(text: str, language_code: str) -> bytes | None:
    headers = {"api-subscription-key": settings.sarvam_api_key, "Content-Type": "application/json"}
    payload = {
        "text": text[:2500],  # bulbul:v3 hard limit
        "target_language_code": language_code,
        "model": "bulbul:v3",
        "speaker": settings.sarvam_tts_speaker,
        "output_audio_codec": "opus",
        "speech_sample_rate": "24000",  # opus only supports 8000/12000/16000/24000/48000
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{SARVAM_BASE_URL}/text-to-speech", headers=headers, json=payload)
            resp.raise_for_status()
            audios = resp.json().get("audios") or []
            return base64.b64decode(audios[0]) if audios else None
    except httpx.HTTPError:
        return None


async def synthesize_speech(text: str, language_code: str | None = None) -> bytes | None:
    """
    Text-to-speech via Sarvam's Bulbul model. Returns Opus-encoded audio
    bytes, or None if Sarvam isn't configured or the call fails.

    Explicitly requests Opus (via output_audio_codec) rather than the
    default WAV — confirmed live that WhatsApp's Business API rejects WAV
    voice-note uploads outright ("File is not allowed by WhatsApp"), even
    though the upload call to Wati itself succeeds. Opus is what WhatsApp's
    own voice notes actually use (confirmed against real incoming .opus
    voice notes from students).

    Regenerates once if the output has a detectable noise/click glitch —
    confirmed via a real glitchy voice note that Sarvam's TTS occasionally
    produces audible artifacts unrelated to any specific text pattern (ruled
    out via A/B testing), so a retry is the practical mitigation rather than
    trying to avoid some specific triggering character.
    """
    if not settings.sarvam_api_key:
        return None

    lang = language_code or settings.sarvam_language_code
    audio = await _call_tts_api(text, lang)
    if audio and has_audio_glitch(audio):
        retry = await _call_tts_api(text, lang)
        if retry:
            audio = retry
    return audio


async def detect_language(text: str) -> tuple[str, str] | None:
    """
    Identify the language and script of a piece of text, e.g. ("hi-IN", "Deva").
    Returns None if Sarvam isn't configured or the call fails.

    Script matters more than the guessed language code for Romanized (Latin
    script) text: detecting the *language* of text typed in Latin letters is
    inherently ambiguous (e.g. "kyunki jagah" phonetically resembles several
    Indian languages), so callers should treat a "Latin" script result as low
    confidence and prefer sticking with an already-known language rather than
    switching on a guess.
    """
    if not settings.sarvam_api_key:
        return None

    headers = {"api-subscription-key": settings.sarvam_api_key, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{SARVAM_BASE_URL}/text-lid", headers=headers, json={"input": text[:1000]}
            )
            resp.raise_for_status()
            data = resp.json()
            language_code = data.get("language_code")
            script_code = data.get("script_code")
            if not language_code:
                return None
            return language_code, script_code
    except httpx.HTTPError:
        return None


async def _translate_line(client: httpx.AsyncClient, headers: dict, line: str, source: str, target: str) -> str:
    payload = {
        "input": line[:2000],
        "source_language_code": source,
        "target_language_code": target,
        "model": "mayura:v1",
        "mode": "modern-colloquial",
    }
    try:
        resp = await client.post(f"{SARVAM_BASE_URL}/translate", headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json().get("translated_text") or line
    except httpx.HTTPError:
        return line  # fall back to the untranslated (English) line rather than dropping it


async def translate_text(text: str, source_language_code: str, target_language_code: str) -> str | None:
    """
    Translate text between languages using Sarvam's Indic-specialized model.
    Returns None if Sarvam isn't configured entirely; on a per-line failure,
    that line falls back to English rather than the whole reply failing.

    Translates line-by-line (not the whole message in one call) because
    translation reorders words — a single call both breaks *bold* markers
    (the asterisks end up stranded when the wrapped word moves) and tends to
    collapse paragraph/bullet newlines into one run-on block. Stripping
    markdown emphasis first and preserving each line's boundary keeps the
    original message structure (paragraphs, bullets) intact and readable.
    """
    if not settings.sarvam_api_key:
        return None

    headers = {"api-subscription-key": settings.sarvam_api_key, "Content-Type": "application/json"}
    lines = text.split("\n")

    async with httpx.AsyncClient(timeout=20) as client:
        translated_lines = await asyncio.gather(
            *[
                _translate_line(client, headers, _MARKDOWN_EMPHASIS.sub("", line), source_language_code, target_language_code)
                if line.strip()
                else asyncio.sleep(0, result="")
                for line in lines
            ]
        )

    return "\n".join(translated_lines)
