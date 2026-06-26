"""ACC speech — local, sovereign STT/TTS for the assistant's voice interface.

Greenfield (integrations pillar 3). All audio stays ON-DEVICE (C2 sovereignty):
Whisper for STT (faster-whisper on GPU/DC, whisper.cpp on edge/CPU — same
weights, different runtime) and Piper for TTS. Model weights are an OPT-IN extra
(``pip install 'acc[speech]'``), so the 2 GB edge floor (C1) is untouched by
default. See ACC-PR/Proposals/PR-PROPOSAL-C.
"""

from acc.speech.bridge import VoiceBridge
from acc.speech.stt import STTBackend, select_stt
from acc.speech.tts import TTSBackend, select_tts

__all__ = ["STTBackend", "TTSBackend", "VoiceBridge", "select_stt", "select_tts"]
