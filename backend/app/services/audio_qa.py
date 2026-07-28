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
