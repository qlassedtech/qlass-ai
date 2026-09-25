import io
import logging
import shutil
import subprocess

import librosa
import numpy as np

logger = logging.getLogger(__name__)

FRAME_SECONDS = 0.05
ENERGY_SPIKE_RATIO = 3.0
ENERGY_SPIKE_MIN = 0.05
# Broadband/click artifacts measured empirically at ~0.008-0.077 spectral
# flatness; normal voiced speech at the same amplitude sits near ~0.001-0.005
# (flatness near 0 = harmonic/tonal, near 1 = pure noise).
FLATNESS_NOISE_THRESHOLD = 0.02

# Last-resort duration estimate when nothing can decode the bytes (see
# get_duration_seconds): seconds = bytes * 8 / bitrate. Opus voice from a
# browser MediaRecorder / WhatsApp voice note typically encodes at
# 24-32 kbps; using the LOW end of that range deliberately over-estimates
# the duration slightly (a 30 kB clip reads as 10 s rather than 7.5 s), so
# an undecodable clip is billed conservatively against the student rather
# than under-billed — the failure mode this replaces was billing ZERO.
ESTIMATED_VOICE_BITRATE_BPS = 24_000
FFPROBE_TIMEOUT_SECONDS = 10


def _ffprobe_duration_seconds(audio_bytes: bytes) -> float | None:
    """Container-level duration via ffprobe (stdin), or None if unavailable/undecodable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", "pipe:0"],
            input=audio_bytes, capture_output=True, timeout=FFPROBE_TIMEOUT_SECONDS, check=False,
        )
        value = float(completed.stdout.decode("ascii", "ignore").strip().splitlines()[0])
        return value if value > 0 else None
    except Exception:
        return None


def get_duration_seconds(audio_bytes: bytes) -> float:
    """
    Audio length in seconds, used to bill Sarvam STT (billed per minute).

    Never returns 0 for non-empty bytes. librosa/soundfile decode covers
    WAV/OGG/MP3/FLAC, but NOT the audio/webm;codecs=opus a browser
    MediaRecorder produces (the voice call and the web app's voice notes)
    — confirmed live (audit, Sept 2026) that every one of those returned
    0.0 here, so record_minute_usage deducted nothing and STT for those
    channels was never billed. Fallback chain: librosa -> ffprobe (if the
    binary is installed) -> a conservative byte-length estimate (see
    ESTIMATED_VOICE_BITRATE_BPS). Which one was used is logged at INFO so
    a deployment missing ffprobe is visible in the logs, not just in the
    margins.
    """
    if not audio_bytes:
        return 0.0
    try:
        y, sr = librosa.load(io.BytesIO(audio_bytes), sr=None)
        if sr and len(y):
            duration = len(y) / sr
            logger.info("audio duration %.2fs via librosa (%d bytes)", duration, len(audio_bytes))
            return duration
    except Exception:
        pass
    duration = _ffprobe_duration_seconds(audio_bytes)
    if duration is not None:
        logger.info("audio duration %.2fs via ffprobe (%d bytes)", duration, len(audio_bytes))
        return duration
    duration = len(audio_bytes) * 8 / ESTIMATED_VOICE_BITRATE_BPS
    logger.info(
        "audio duration %.2fs ESTIMATED from %d bytes at %d bps (undecodable by librosa/ffprobe)",
        duration, len(audio_bytes), ESTIMATED_VOICE_BITRATE_BPS,
    )
    return duration


# Rough male/female fundamental-frequency (F0) crossover — typical adult male
# speech sits ~85-180Hz, typical adult female ~165-255Hz. This is a coarse
# heuristic, not a reliable classifier (school-age voices in particular can
# sit well outside these adult ranges), but it's a real, working signal
# rather than nothing — accepted deliberately, error rate and all, so the
# opposite-gender voice selection has *something* to go on for a first
# voice note.
GENDER_PITCH_THRESHOLD_HZ = 165.0


def detect_gender_from_pitch(audio_bytes: bytes) -> str | None:
    """
    Estimate speaker gender from the average pitch (F0) of a voice note.
    Returns "male", "female", or None if pitch couldn't be estimated (e.g.
    too short, too noisy, or undecodable) — callers should treat None as
    "unknown" and not guess further.
    """
    try:
        y, sr = librosa.load(io.BytesIO(audio_bytes), sr=None)
        f0, _voiced_flag, _voiced_probs = librosa.pyin(
            y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C7"), sr=sr
        )
        voiced_f0 = f0[~np.isnan(f0)]
        if len(voiced_f0) == 0:
            return None
        mean_f0 = float(np.mean(voiced_f0))
        return "male" if mean_f0 < GENDER_PITCH_THRESHOLD_HZ else "female"
    except Exception:
        return None


def has_audio_glitch(audio_bytes: bytes) -> bool:
    """
    Detects the class of noise/click artifact found in Sarvam's Opus TTS
    output: a short burst of energy 3x+ louder than its neighboring frames,
    with a noise-like (high spectral flatness) rather than voice-like
    (harmonic, low flatness) spectrum — confirmed against a real glitchy
    voice note and ruled out as tied to any specific character/pattern via
    A/B testing, so this appears to be occasional stochastic TTS noise
    rather than a deterministic trigger we can just avoid in the text.
    """
    try:
        y, sr = librosa.load(io.BytesIO(audio_bytes), sr=None)
    except Exception:
        return False  # can't analyze -> don't block sending over a decode issue

    frame_len = int(sr * FRAME_SECONDS)
    if frame_len <= 0 or len(y) < frame_len * 3:
        return False

    n_frames = (len(y) - frame_len) // frame_len
    energies = np.array(
        [np.sqrt(np.mean(y[i * frame_len : (i + 1) * frame_len] ** 2)) for i in range(n_frames)]
    )

    for i in range(1, len(energies) - 1):
        if energies[i] > ENERGY_SPIKE_RATIO * max(energies[i - 1], energies[i + 1]) and energies[i] > ENERGY_SPIKE_MIN:
            segment = y[i * frame_len : (i + 1) * frame_len]
            flatness = float(np.mean(librosa.feature.spectral_flatness(y=segment, n_fft=frame_len)))
            if flatness > FLATNESS_NOISE_THRESHOLD:
                return True
    return False
