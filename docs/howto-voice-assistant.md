# How-to — voice assistant (Whisper STT + Piper TTS, on-device)

Integration **pillar 3 (interact)** — talk to the assistant. Greenfield. Design:
`ACC-PR/Proposals/PR-PROPOSAL-C`. Everything is **on-device** (C2 sovereignty);
no audio leaves the machine.

## The interface choice

| Role | Choice | Where |
|---|---|---|
| STT | **Whisper** — `faster-whisper` (GPU/DC) / `whisper.cpp` (edge/CPU) | runtime selected by `deploy_mode` |
| TTS | **Piper** | local, tiny |
| VAD | `silero-vad` | endpointing (push-to-talk) |

Same Whisper weights, different runtime — one mental model. Cloud STT/TTS is
rejected on sovereignty grounds.

## Install (opt-in — protects the edge memory floor)

```bash
pip install 'acc[speech]'          # faster-whisper + piper
pip install pywhispercpp           # (edge/CPU) whisper.cpp runtime
pip install sounddevice            # mic/speaker capture for the daemon
```

Config:
```
ACC_SPEECH_STT_BACKEND=auto        # auto|faster_whisper|whisper_cpp
ACC_SPEECH_STT_MODEL=base          # tiny|base|small|medium (size vs the budget)
ACC_SPEECH_TTS_VOICE=en_US-amy-low
ACC_VOICE_TARGET_ROLE=assistant
```

## Run

```bash
acc-channel-voice                  # push-to-talk loop → assistant → spoken reply
```

The daemon: mic → VAD → Whisper → `channel.send(assistant)` → reply →
Piper → speaker. It uses the **same governed text path** as the TUI — voice adds
no new action surface; the membrane, audit, and oversight apply unchanged.

## As skills (any role can use them)

- `speech_transcribe` — audio file → text.
- `speech_synthesize` — text → WAV.

Both are granted to the assistant; other roles can be granted them later.

## Privacy

Push-to-talk by default (no hot mic). Risk-bearing spoken commands (anything that
triggers a write/oversight item) should get a spoken confirmation read-back
(roadmap). All audio + transcripts stay on the device.

## Test

```bash
python -m pytest tests/test_speech.py -q
```
Hermetic — backends are faked, no model downloads: STT/TTS selection, both
skills, and the `VoiceBridge` round-trip.
