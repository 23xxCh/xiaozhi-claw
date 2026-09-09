"""Bounded Doubao 3.0 probe; never prints credentials, transcripts or raw audio.

Default checks authenticated session setup only. --audio accepts one explicitly
chosen 16 kHz mono 16-bit WAV and sends it to the configured Doubao endpoint.
"""

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.voice_routes import DOUBAO_VOICE, DOUBAO_VOICES  # noqa: E402
from backend.realtime.doubao import DoubaoConfig, DoubaoRealtimeBackend  # noqa: E402


async def probe(args):
    settings = Settings()
    backend = None
    result = {"provider": "doubao", "model": args.model, "voice": args.voice,
              "authenticated_session": False,
              "audio_tested": False, "device_tested": False}
    started = time.monotonic()
    try:
        backend = await DoubaoRealtimeBackend.open(
            DoubaoConfig(api_key=settings.doubao_api_key, url=settings.doubao_realtime_url,
                         model=args.model, voice=args.voice, timeout_seconds=10),
            instructions="你是语音接口测试助手，请用简短中文回答，不调用工具。",
        )
        result["authenticated_session"] = True
        result["session_setup_ms"] = round((time.monotonic() - started) * 1000)
        if args.audio:
            with wave.open(str(args.audio), "rb") as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                    raise ValueError("expected-mono-s16le-16000-wav")
                if wav.getnframes() > 16000 * 15:
                    raise ValueError("audio-exceeds-15-seconds")
                pcm = wav.readframes(wav.getnframes())
            generation = backend.begin_turn("bounded-probe")

            async def upload():
                for offset in range(0, len(pcm), 640):
                    await backend.send_audio(pcm[offset:offset + 640], generation=generation)
                    await asyncio.sleep(0.02)
                await backend.end_input(generation=generation)

            audio_bytes = 0
            output_pcm = bytearray()
            saw_done = False
            events = []
            first_audio_at = None
            upload_task = asyncio.create_task(upload())
            try:
                async with asyncio.timeout(45):
                    async for event in backend.events():
                        if event.type not in events:
                            events.append(event.type)
                        audio_bytes += len(event.audio)
                        if event.audio and first_audio_at is None:
                            first_audio_at = time.monotonic()
                        if args.save_audio:
                            if audio_bytes > 24000 * 2 * 60:
                                raise ValueError("response-exceeds-60-seconds")
                            output_pcm.extend(event.audio)
                        saw_done |= event.type == "done"
                        if event.type == "usage":
                            result["usage"] = event.usage
                    await upload_task
            finally:
                upload_task.cancel()
                await asyncio.gather(upload_task, return_exceptions=True)
            result.update(audio_tested=True, response_complete=saw_done,
                          output_pcm_bytes=audio_bytes, event_types=events,
                          first_audio_from_probe_start_ms=(
                              round((first_audio_at - started) * 1000) if first_audio_at else None
                          ))
            if args.save_audio and output_pcm:
                args.save_audio.parent.mkdir(parents=True, exist_ok=True)
                with wave.open(str(args.save_audio), "wb") as wav:
                    wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                    wav.writeframes(output_pcm)
        result["status"] = "passed" if not args.audio or saw_done and audio_bytes else "failed"
    except Exception as error:
        result["status"] = "failed"
        result["error_code"] = getattr(error, "code", type(error).__name__)
    finally:
        if backend is not None:
            await backend.close()
    print(json.dumps(result, ensure_ascii=False))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--model", default="1.2.6.1")
    parser.add_argument("--voice", choices=sorted(DOUBAO_VOICES), default=DOUBAO_VOICE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--save-audio", type=Path,
                        help="Save diagnostic response WAV; use only with approved test audio")
    raise SystemExit(asyncio.run(probe(parser.parse_args())))
