"""app.services.audio_qa.get_duration_seconds must never bill zero for real audio bytes."""
import io
import wave

import numpy as np
import pytest

from app.services import audio_qa


def _wav_bytes(seconds: float, sample_rate: int = 16000) -> bytes:
    samples = (np.sin(np.linspace(0, 440 * 2 * np.pi * seconds, int(sample_rate * seconds))) * 12000).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return buffer.getvalue()


def test_wav_duration_is_decoded_exactly():
    assert audio_qa.get_duration_seconds(_wav_bytes(1.5)) == pytest.approx(1.5, abs=0.01)


def test_undecodable_bytes_fall_back_to_a_conservative_estimate_not_zero(monkeypatch):
    garbage = bytes(range(256)) * (30_000 // 256) + b"\x00" * (30_000 % 256)
    assert len(garbage) == 30_000
    monkeypatch.setattr(audio_qa, "_ffprobe_duration_seconds", lambda audio_bytes: None)  # as if ffprobe weren't installed

    duration = audio_qa.get_duration_seconds(garbage)

    assert duration == pytest.approx(30_000 * 8 / audio_qa.ESTIMATED_VOICE_BITRATE_BPS)  # 10s at 24 kbps
    assert duration > 0


def test_ffprobe_is_tried_before_the_estimate(monkeypatch):
    monkeypatch.setattr(audio_qa, "_ffprobe_duration_seconds", lambda audio_bytes: 7.25)
    assert audio_qa.get_duration_seconds(b"\x1aE\xdf\xa3not-really-webm" * 100) == 7.25


def test_empty_bytes_are_still_zero():
    assert audio_qa.get_duration_seconds(b"") == 0.0
