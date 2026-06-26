"""``acc-channel-voice`` — the assistant's spoken interface daemon.

Push-to-talk loop: capture audio → STT (Whisper) → send through the SAME
governed text channel the TUI/Slack use → receive the reply → TTS (Piper) →
play. All on-device (C2). Wake-word is an opt-in follow-up.

Everything heavy (audio capture, Whisper/Piper, NATS) is imported INSIDE
:func:`main` so this module imports cleanly without the optional ``[speech]``
extra — the entry point resolves anywhere; only running it needs a mic + the
extra. The reusable, CI-tested core is :class:`acc.speech.VoiceBridge`.
"""

from __future__ import annotations

import asyncio
import os
import sys


async def _run() -> int:
    from acc.speech import VoiceBridge, select_stt, select_tts
    from acc.tui.client import NATSObserver
    from acc.channels.tui import TUIPromptChannel

    nats_url = os.environ.get("ACC_NATS_URL", "nats://localhost:4222")
    cid = os.environ.get("ACC_COLLECTIVE_ID", "sol-01")
    target_role = os.environ.get("ACC_VOICE_TARGET_ROLE", "assistant")

    try:
        stt, tts = select_stt(), select_tts()
    except Exception as exc:  # noqa: BLE001
        print(f"acc-channel-voice: speech extra not ready ({exc}). "
              f"Install with: pip install 'acc[speech]'", file=sys.stderr)
        return 2

    obs = NATSObserver(nats_url=nats_url, collective_id=cid, update_queue=asyncio.Queue())
    await obs.connect()
    channel = TUIPromptChannel(obs, collective_id=cid)
    bridge = VoiceBridge(stt, tts, channel, target_role=target_role)

    try:
        import sounddevice  # noqa: F401, PLC0415 — audio capture
    except Exception:
        print("acc-channel-voice: `sounddevice` not installed — push-to-talk "
              "capture unavailable. `pip install sounddevice`.", file=sys.stderr)
        await obs.close()
        return 2

    print(f"acc-channel-voice ready · STT={stt.name} TTS={tts.name} → {target_role}. "
          "Press Enter to talk, Ctrl-C to quit.")
    try:
        while True:
            audio = await asyncio.to_thread(_capture_push_to_talk)   # see below
            if audio is None:
                continue
            turn = await bridge.speak_turn(audio)
            print(f"you: {turn.transcript}\nassistant: {turn.reply_text}")
            await asyncio.to_thread(_play, turn.reply_audio)
    except KeyboardInterrupt:
        pass
    finally:
        await obs.close()
    return 0


def _capture_push_to_talk():  # pragma: no cover — needs a microphone
    """Record from the default mic until the operator releases push-to-talk.
    Returns WAV bytes (or None). Implemented at deploy time per host; the loop
    above is runtime/hardware-specific and not exercised in CI."""
    raise NotImplementedError(
        "wire host audio capture (sounddevice) here — see docs/howto-voice-assistant.md"
    )


def _play(wav_bytes: bytes) -> None:  # pragma: no cover — needs a speaker
    raise NotImplementedError("wire host audio playback here")


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
