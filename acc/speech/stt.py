"""Speech-to-text backends — Whisper, runtime-selected by deploy mode.

* ``faster-whisper`` (CTranslate2 int8) — GPU / DC.
* ``whisper.cpp`` (quantized GGML) — edge / CPU.

Same weights, different runtime. Heavy libs are imported LAZILY inside
``transcribe`` so the module (and ``select_stt``) cost ~0 until audio is
actually transcribed — and the optional ``[speech]`` extra need not be
installed for the rest of ACC to import this module.
"""

from __future__ import annotations

import abc
import asyncio
import os
from pathlib import Path


class STTBackend(abc.ABC):
    """Transcribe audio (a WAV/PCM file path or raw bytes) to text."""

    name: str = "stt"

    @abc.abstractmethod
    async def transcribe(self, audio: str | Path | bytes, *, language: str | None = None) -> str:
        ...


class FasterWhisperSTT(STTBackend):
    name = "faster_whisper"

    def __init__(self, model: str = "base", *, compute_type: str = "int8", device: str = "auto") -> None:
        self._model_name = model
        self._compute_type = compute_type
        self._device = device
        self._model = None  # lazy

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # noqa: PLC0415 — optional extra
            self._model = WhisperModel(
                self._model_name, device=self._device, compute_type=self._compute_type,
            )
        return self._model

    async def transcribe(self, audio, *, language=None) -> str:
        def _run() -> str:
            model = self._load()
            src = audio if isinstance(audio, (str, Path)) else _bytes_to_tmp(audio)
            segments, _info = model.transcribe(str(src), language=language)
            return " ".join(seg.text.strip() for seg in segments).strip()

        return await asyncio.to_thread(_run)


class WhisperCppSTT(STTBackend):
    name = "whisper_cpp"

    def __init__(self, model: str = "base") -> None:
        self._model_name = model
        self._model = None

    def _load(self):
        if self._model is None:
            from pywhispercpp.model import Model  # noqa: PLC0415 — optional extra
            self._model = Model(self._model_name)
        return self._model

    async def transcribe(self, audio, *, language=None) -> str:
        def _run() -> str:
            model = self._load()
            src = audio if isinstance(audio, (str, Path)) else _bytes_to_tmp(audio)
            segs = model.transcribe(str(src), language=(language or "auto"))
            return " ".join(s.text.strip() for s in segs).strip()

        return await asyncio.to_thread(_run)


def _bytes_to_tmp(data: bytes) -> Path:
    import tempfile  # noqa: PLC0415
    f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    f.write(data)
    f.flush()
    f.close()
    return Path(f.name)


def select_stt(
    *, deploy_mode: str | None = None, backend: str | None = None, model: str | None = None,
) -> STTBackend:
    """Pick an STT backend from config / deploy mode.

    ``ACC_SPEECH_STT_BACKEND`` = ``auto`` (default) | ``faster_whisper`` |
    ``whisper_cpp``. ``auto`` → whisper.cpp on edge (CPU), faster-whisper
    elsewhere (GPU). ``ACC_SPEECH_STT_MODEL`` (default ``base``).
    """
    backend = (backend or os.environ.get("ACC_SPEECH_STT_BACKEND") or "auto").lower()
    model = model or os.environ.get("ACC_SPEECH_STT_MODEL") or "base"
    deploy_mode = (deploy_mode or os.environ.get("ACC_DEPLOY_MODE") or "").lower()
    if backend == "auto":
        backend = "whisper_cpp" if deploy_mode == "edge" else "faster_whisper"
    if backend == "whisper_cpp":
        return WhisperCppSTT(model=model)
    return FasterWhisperSTT(model=model)
