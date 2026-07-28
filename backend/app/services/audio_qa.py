import io

import librosa
import numpy as np

FRAME_SECONDS = 0.05
ENERGY_SPIKE_RATIO = 3.0
ENERGY_SPIKE_MIN = 0.05
# Broadband/click artifacts measured empirically at ~0.008-0.077 spectral
# flatness; normal voiced speech at the same amplitude sits near ~0.001-0.005
# (flatness near 0 = harmonic/tonal, near 1 = pure noise).
FLATNESS_NOISE_THRESHOLD = 0.02


def get_duration_seconds(audio_bytes: bytes) -> float:
    """Audio length in seconds, used to bill Sarvam STT (billed per minute). Returns 0.0 if undecodable."""
    try:
        y, sr = librosa.load(io.BytesIO(audio_bytes), sr=None)
        return len(y) / sr if sr else 0.0
    except Exception:
        return 0.0


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
