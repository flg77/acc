"""VoiceBridge — one spoken turn around a text PromptChannel.

The assistant's voice interface is a thin bridge, NOT a new channel type: audio
in → STT → ``channel.send`` (the same governed path the TUI/Slack use) →
``channel.receive`` → TTS → audio out. The ``acc-channel-voice`` daemon loops
this with VAD + push-to-talk. Keeping it a composition of (STT, text channel,
TTS) means the membrane / audit / oversight apply unchanged — voice adds no new
external-action surface (C3).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from acc.speech.stt import STTBackend
from acc.speech.tts import TTSBackend


@dataclasses.dataclass
class VoiceTurn:
    transcript: str        # what the operator said (STT)
    reply_text: str        # the agent's reply text
    reply_audio: bytes     # the spoken reply (TTS WAV)
    task_id: str
    blocked: bool = False
    block_reason: str = ""


class VoiceBridge:
    """Compose an STT backend, a text :class:`PromptChannel`, and a TTS backend
    into one ``speak_turn`` round-trip."""

    def __init__(
        self,
        stt: STTBackend,
        tts: TTSBackend,
        channel: Any,                 # acc.channels.base.PromptChannel
        *,
        target_role: str = "assistant",
        timeout_s: float = 120.0,
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._channel = channel
        self._target_role = target_role
        self._timeout_s = timeout_s

    async def speak_turn(self, audio_in: bytes | str, *, language: str | None = None) -> VoiceTurn:
        transcript = await self._stt.transcribe(audio_in, language=language)
        if not transcript.strip():
            silence = await self._tts.synthesize("I didn't catch that.")
            return VoiceTurn("", "I didn't catch that.", silence, task_id="", blocked=False)
        task_id = await self._channel.send(transcript, target_role=self._target_role)
        reply = await self._channel.receive(task_id, timeout=self._timeout_s)
        reply_text = getattr(reply, "output", "") or ""
        blocked = bool(getattr(reply, "blocked", False))
        reply_audio = await self._tts.synthesize(reply_text or "Done.")
        return VoiceTurn(
            transcript=transcript,
            reply_text=reply_text,
            reply_audio=reply_audio,
            task_id=task_id,
            blocked=blocked,
            block_reason=getattr(reply, "block_reason", "") or "",
        )
