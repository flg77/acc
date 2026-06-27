"""speech_synthesize — text → speech WAV via acc.speech (Piper).

Imports the ``tts`` module (not the bound name) so tests can monkeypatch
``acc.speech.tts.select_tts`` with a fake backend (no model in CI).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from acc.skills import Skill
from acc.skills.skill_runtime import SkillInvocationError
from acc.speech import tts as _tts


class SpeechSynthesizeSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            backend = _tts.select_tts()
            audio = await backend.synthesize(args["text"])
        except Exception as exc:  # noqa: BLE001
            raise SkillInvocationError(f"speech_synthesize: {type(exc).__name__}: {exc}") from exc
        out = args.get("out_path")
        if not out:
            f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            out = f.name
            f.close()
        Path(out).write_bytes(audio)
        return {"audio_path": str(out), "bytes": len(audio)}
