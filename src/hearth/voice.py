"""Voice input: local speech-to-text feeding the chat box.

Ollama's chat API accepts text and images only, so audio cannot be sent to
the local model directly. Instead a small Whisper model (faster-whisper,
int8 on CPU) transcribes the microphone ON THIS MACHINE and the transcript
goes to the language model like typed text — nothing leaves the device.

The extra packages are optional (``pip install "hearth[voice]"``); without
them the mic button explains what to install instead of failing. The speech
model (~75 MB) is downloaded once, only after the user agrees — Hearth never
downloads models silently.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
MAX_SECONDS = 120  # hard cap ≈ 7 MB of float32 audio
MIN_SECONDS = 0.4
DEFAULT_MODEL = "base"  # ~75 MB download, ~150 MB RAM while transcribing

INSTALL_HINT = (
    "Voice input needs optional packages that are not installed. "
    'Run: .venv/bin/pip install "hearth[voice]"  (installs faster-whisper, '
    "sounddevice, numpy), then restart Hearth."
)


class VoiceError(RuntimeError):
    """Voice capture/transcription failed; message is safe to show the user."""


class VoiceInput:
    """Microphone capture + local Whisper transcription.

    ``sd_module`` and ``model_factory`` exist for tests, which inject fakes;
    the app uses the real sounddevice / faster-whisper lazily.
    """

    def __init__(
        self,
        model_dir: Path,
        model_name: str = DEFAULT_MODEL,
        sd_module: Any | None = None,
        model_factory: Any | None = None,
    ):
        self._model_dir = Path(model_dir)
        self._model_name = model_name
        self._sd = sd_module
        self._model_factory = model_factory
        self._model: Any | None = None
        self._stream: Any | None = None
        self._frames: list[Any] = []
        self._captured_samples = 0

    # -- capability probes --------------------------------------------------

    @property
    def available(self) -> bool:
        if self._sd is not None and self._model_factory is not None:
            return True
        return all(
            importlib.util.find_spec(name) is not None
            for name in ("faster_whisper", "sounddevice", "numpy")
        )

    def model_ready(self) -> bool:
        """True when transcription will not trigger a download."""
        if self._model is not None or self._model_factory is not None:
            return True
        return any(self._model_dir.rglob("model.bin"))

    @property
    def recording(self) -> bool:
        return self._stream is not None

    # -- recording ----------------------------------------------------------

    def start(self) -> None:
        if self._stream is not None:
            return
        sd = self._sounddevice()
        self._frames = []
        self._captured_samples = 0
        sample_cap = SAMPLE_RATE * MAX_SECONDS

        def callback(indata, frames, time_info, status) -> None:  # noqa: ARG001
            # Runs on the audio thread; list append is thread-safe under the GIL.
            if self._captured_samples < sample_cap:
                self._frames.append(indata.copy())
                self._captured_samples += len(indata)

        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise VoiceError(
                f"Could not open the microphone: {exc}. "
                "Check the OS microphone permission for Hearth."
            ) from exc

    def stop(self):
        """Stop recording and return the captured audio (float32 mono 16 kHz)."""
        if self._stream is None:
            raise VoiceError("Not recording")
        stream, self._stream = self._stream, None
        stream.stop()
        stream.close()

        import numpy as np

        frames, self._frames = self._frames, []
        if not frames:
            raise VoiceError("No audio was captured — is a microphone connected?")
        audio = np.concatenate(frames).reshape(-1).astype("float32")
        if len(audio) < SAMPLE_RATE * MIN_SECONDS:
            raise VoiceError("The recording was too short — speak, then click stop.")
        return audio

    def cancel(self) -> None:
        """Discard an in-progress recording without transcribing."""
        if self._stream is not None:
            stream, self._stream = self._stream, None
            stream.stop()
            stream.close()
        self._frames = []

    # -- transcription -------------------------------------------------------

    def transcribe_blocking(self, audio) -> str:
        """CPU-heavy; call via asyncio.to_thread. May download the model on
        first use — gate that behind user consent (see ``model_ready``)."""
        model = self._load_model()
        segments, _info = model.transcribe(audio, beam_size=1)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if not text:
            raise VoiceError("No speech detected in the recording.")
        return text

    # -- lazy backends -------------------------------------------------------

    def _sounddevice(self) -> Any:
        if self._sd is None:
            try:
                import sounddevice
            except (ImportError, OSError) as exc:  # OSError: PortAudio missing
                raise VoiceError(INSTALL_HINT) from exc
            self._sd = sounddevice
        return self._sd

    def _load_model(self) -> Any:
        if self._model is None:
            if self._model_factory is not None:
                self._model = self._model_factory()
            else:
                self._model = self._default_model()
        return self._model

    def _default_model(self) -> Any:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise VoiceError(INSTALL_HINT) from exc
        self._model_dir.mkdir(parents=True, exist_ok=True)
        log.info("Loading Whisper model '%s' (int8, CPU)", self._model_name)
        return WhisperModel(
            self._model_name,
            device="cpu",
            compute_type="int8",
            download_root=str(self._model_dir),
        )
