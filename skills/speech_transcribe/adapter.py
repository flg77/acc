"""speech_transcribe — local audio → text via acc.speech (Whisper).

Imports the ``stt`` module (not the bound name) so tests can monkeypatch
``acc.speech.stt.select_stt`` with a fake backend (no model download in CI).
"""

from __future__ import annotations

from typing import Any

from acc.skills import Skill
from acc.skills.skill_runtime import SkillInvocationError
from acc.speech import stt as _stt


class SpeechTranscribeSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            backend = _stt.select_stt()
            text = await backend.transcribe(args["audio_path"], language=args.get("language"))
        except Exception as exc:  # noqa: BLE001
            raise SkillInvocationError(f"speech_transcribe: {type(exc).__name__}: {exc}") from exc
        return {"text": text}
