"""Text-to-speech backends — Piper (local, fast, fully on-device).

Lazy import like the STT backends so the optional ``[speech]`` extra need not be
installed for the rest of ACC. A ``TTSBackend`` interface keeps a future
voice-engine swap cheap.
"""

from __future__ import annotations

import abc
import asyncio
import os
from pathlib import Path


class TTSBackend(abc.ABC):
    """Synthesize speech audio (WAV bytes) from text."""

    name: str = "tts"

    @abc.abstractmethod
    async def synthesize(self, text: str) -> bytes:
        ...


class PiperTTS(TTSBackend):
    name = "piper"

    def __init__(self, voice: str = "en_US-amy-low") -> None:
        self._voice = voice
        self._engine = None  # lazy

    def _load(self):
        if self._engine is None:
            from piper import PiperVoice  # noqa: PLC0415 — optional extra
            self._engine = PiperVoice.load(self._voice)
        return self._engine

    async def synthesize(self, text: str) -> bytes:
        def _run() -> bytes:
            import io
            import wave
            voice = self._load()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                voice.synthesize(text, wf)
            return buf.getvalue()

        return await asyncio.to_thread(_run)


def select_tts(*, backend: str | None = None, voice: str | None = None) -> TTSBackend:
    """Pick a TTS backend. ``ACC_SPEECH_TTS_VOICE`` selects the Piper voice."""
    backend = (backend or os.environ.get("ACC_SPEECH_TTS_BACKEND") or "piper").lower()
    voice = voice or os.environ.get("ACC_SPEECH_TTS_VOICE") or "en_US-amy-low"
    # Only Piper today; the interface keeps a swap cheap.
    return PiperTTS(voice=voice)
