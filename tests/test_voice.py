"""Voice input: capture lifecycle, caps, transcription joining, and the
graceful path when optional voice packages are missing. Uses injected fakes —
no microphone, no Whisper download."""

import importlib.util
from types import SimpleNamespace

import numpy as np
import pytest

from hearth.voice import SAMPLE_RATE, VoiceError, VoiceInput


class FakeStream:
    def __init__(self, callback):
        self.callback = callback
        self.started = self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


class FakeSoundDevice:
    def __init__(self):
        self.stream = None

    def InputStream(self, samplerate, channels, dtype, callback):  # noqa: N802
        assert samplerate == SAMPLE_RATE and channels == 1 and dtype == "float32"
        self.stream = FakeStream(callback)
        return self.stream

    def feed(self, seconds: float):
        samples = int(SAMPLE_RATE * seconds)
        self.stream.callback(np.zeros((samples, 1), dtype="float32"), samples, None, None)


def _voice(tmp_path, segments=None) -> tuple[VoiceInput, FakeSoundDevice]:
    sd = FakeSoundDevice()
    model = SimpleNamespace(transcribe=lambda audio, beam_size: (segments or [], None))
    return VoiceInput(tmp_path, sd_module=sd, model_factory=lambda: model), sd


def test_record_stop_returns_audio(tmp_path):
    voice, sd = _voice(tmp_path)
    voice.start()
    assert voice.recording and sd.stream.started
    sd.feed(1.0)
    sd.feed(0.5)
    audio = voice.stop()
    assert not voice.recording and sd.stream.closed
    assert len(audio) == int(SAMPLE_RATE * 1.5)
    assert audio.dtype == np.float32


def test_too_short_recording_rejected(tmp_path):
    voice, sd = _voice(tmp_path)
    voice.start()
    sd.feed(0.1)
    with pytest.raises(VoiceError, match="too short"):
        voice.stop()


def test_recording_capped_at_max_seconds(tmp_path):
    voice, sd = _voice(tmp_path)
    voice.start()
    for _ in range(5):
        sd.feed(30.0)  # 150 s offered; cap is 120 s
    audio = voice.stop()
    assert len(audio) <= SAMPLE_RATE * 121


def test_cancel_discards_audio(tmp_path):
    voice, sd = _voice(tmp_path)
    voice.start()
    sd.feed(2.0)
    voice.cancel()
    assert not voice.recording
    with pytest.raises(VoiceError, match="Not recording"):
        voice.stop()


def test_transcribe_joins_segments(tmp_path):
    segments = [SimpleNamespace(text="  Hello "), SimpleNamespace(text=" world.")]
    voice, _sd = _voice(tmp_path, segments=segments)
    assert voice.transcribe_blocking(np.zeros(SAMPLE_RATE)) == "Hello world."


def test_silence_raises(tmp_path):
    voice, _sd = _voice(tmp_path, segments=[])
    with pytest.raises(VoiceError, match="No speech"):
        voice.transcribe_blocking(np.zeros(SAMPLE_RATE))


def test_model_ready_reflects_cache(tmp_path):
    voice = VoiceInput(tmp_path)
    assert not voice.model_ready()
    cached = tmp_path / "models--x" / "snapshots" / "abc"
    cached.mkdir(parents=True)
    (cached / "model.bin").write_bytes(b"\x00")
    assert voice.model_ready()


def test_missing_packages_give_install_hint(tmp_path):
    if importlib.util.find_spec("sounddevice") is not None:
        pytest.skip("sounddevice installed; the missing-package path is not reachable")
    voice = VoiceInput(tmp_path)
    assert not voice.available
    with pytest.raises(VoiceError, match="hearth\\[voice\\]"):
        voice.start()
