"""Speech interface — STT/TTS selection, the two skills, and the VoiceBridge.

Hermetic: no model weights are downloaded. Backends are faked and
``select_stt``/``select_tts`` are monkeypatched, so the optional ``[speech]``
extra need not be installed to run these.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from acc.skills.registry import SkillRegistry
from acc.speech import stt as stt_mod
from acc.speech import tts as tts_mod
from acc.speech.stt import FasterWhisperSTT, STTBackend, WhisperCppSTT, select_stt
from acc.speech.tts import PiperTTS, TTSBackend, select_tts
from acc.speech.bridge import VoiceBridge


# ---- fakes -----------------------------------------------------------------

class FakeSTT(STTBackend):
    name = "fake_stt"

    def __init__(self, text: str = "hello assistant") -> None:
        self._text = text

    async def transcribe(self, audio, *, language=None) -> str:
        return self._text


class FakeTTS(TTSBackend):
    name = "fake_tts"

    async def synthesize(self, text: str) -> bytes:
        return b"WAV:" + text.encode("utf-8")


class FakeChannel:
    def __init__(self, reply="hi back", blocked=False):
        self._reply, self._blocked, self.sent = reply, blocked, None

    async def send(self, prompt, *, target_role, **kw):
        self.sent = (prompt, target_role)
        return "task-xyz"

    async def receive(self, task_id, *, timeout=120.0):
        return SimpleNamespace(output=self._reply, blocked=self._blocked, block_reason="")


# ---- backend selection -----------------------------------------------------

def test_select_stt_by_deploy_mode():
    assert isinstance(select_stt(deploy_mode="edge"), WhisperCppSTT)
    assert isinstance(select_stt(deploy_mode="rhoai"), FasterWhisperSTT)
    assert isinstance(select_stt(backend="faster_whisper"), FasterWhisperSTT)
    assert isinstance(select_stt(backend="whisper_cpp"), WhisperCppSTT)


def test_select_tts_default_piper():
    assert isinstance(select_tts(), PiperTTS)


# ---- skills ----------------------------------------------------------------

def _reg() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load_from("skills")
    return reg


def test_speech_skills_discovered_medium_risk():
    mans = _reg().manifests()
    for sid in ("speech_transcribe", "speech_synthesize"):
        assert sid in mans and mans[sid].risk_level == "MEDIUM"


@pytest.mark.asyncio
async def test_speech_transcribe_skill(monkeypatch):
    monkeypatch.setattr(stt_mod, "select_stt", lambda **kw: FakeSTT("transcribed text"))
    out = await _reg().invoke("speech_transcribe", {"audio_path": "/tmp/a.wav"})
    assert out == {"text": "transcribed text"}


@pytest.mark.asyncio
async def test_speech_synthesize_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(tts_mod, "select_tts", lambda **kw: FakeTTS())
    out_path = str(tmp_path / "out.wav")
    out = await _reg().invoke("speech_synthesize", {"text": "speak this", "out_path": out_path})
    assert out["audio_path"] == out_path and out["bytes"] > 0
    assert (tmp_path / "out.wav").read_bytes() == b"WAV:speak this"


# ---- VoiceBridge -----------------------------------------------------------

@pytest.mark.asyncio
async def test_voice_bridge_round_trip():
    ch = FakeChannel(reply="the answer is 42")
    bridge = VoiceBridge(FakeSTT("what is the answer"), FakeTTS(), ch, target_role="assistant")
    turn = await bridge.speak_turn(b"<audio>")
    assert turn.transcript == "what is the answer"
    assert ch.sent == ("what is the answer", "assistant")   # routed through the text channel
    assert turn.reply_text == "the answer is 42"
    assert turn.reply_audio == b"WAV:the answer is 42"
    assert turn.task_id == "task-xyz"


@pytest.mark.asyncio
async def test_voice_bridge_handles_empty_transcript():
    bridge = VoiceBridge(FakeSTT(""), FakeTTS(), FakeChannel())
    turn = await bridge.speak_turn(b"<silence>")
    assert turn.transcript == "" and turn.task_id == ""
    assert turn.reply_audio.startswith(b"WAV:")   # spoke a "didn't catch that" prompt


# ---- PiperTTS API-compat (regression: piper-tts >= 1.3 changed synthesize) ----
# No piper install needed — we inject a fake voice via the lazy ``_engine`` slot.

def _write_min_wav(wf):
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(16000)
    wf.writeframes(b"\x00\x00" * 64)


class _FakeVoiceNew:
    """piper-tts >= 1.3: ``synthesize`` yields chunks; ``synthesize_wav`` writes a WAV."""
    def synthesize_wav(self, text, wf):
        _write_min_wav(wf)

    def synthesize(self, text, *a, **k):
        raise AssertionError("must use synthesize_wav when it exists (piper >= 1.3)")


class _FakeVoiceOld:
    """piper-tts 1.2.x: ``synthesize(text, wave_file)`` writes the WAV directly."""
    def synthesize(self, text, wf):
        _write_min_wav(wf)


@pytest.mark.asyncio
async def test_piper_prefers_synthesize_wav_on_modern_piper():
    pt = PiperTTS()
    pt._engine = _FakeVoiceNew()              # bypass the lazy piper import
    out = await pt.synthesize("hello")
    assert out[:4] == b"RIFF" and len(out) > 44   # a real WAV, not an empty buffer


@pytest.mark.asyncio
async def test_piper_falls_back_to_legacy_synthesize():
    pt = PiperTTS()
    pt._engine = _FakeVoiceOld()
    out = await pt.synthesize("hello")
    assert out[:4] == b"RIFF" and len(out) > 44
