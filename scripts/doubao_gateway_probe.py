"""One bounded S2S gateway turn using an explicitly authorized diagnostic WAV.

--input-wav authorizes sending that file to the explicitly selected supplier.
--offline replaces only the supplier socket; both FFmpeg bridges and the gateway
still run. TestClient is an in-process ASGI transport, not a network/TLS test.
All device ACKs are synthetic_device events, never physical audio acceptance.
Only metadata is saved; the temporary database and owned processes are removed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORKER_TIMEOUT_SECONDS = 45


class ProbeFailure(RuntimeError):
    """Local diagnostic codes only, safe to put in the metadata report."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ProbeFailure(code)


def _ffmpeg(executable: str, data: bytes, *arguments: str) -> bytes:
    result = subprocess.run(
        [executable, "-hide_banner", "-loglevel", "error", *arguments],
        input=data,
        capture_output=True,
        timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    _require(result.returncode == 0, "ffmpeg-failed")
    return result.stdout


def _input_packets(path: Path, ffmpeg: str) -> tuple[list[bytes], int]:
    from backend.app.audio_formats import ogg_opus_packets

    with wave.open(str(path), "rb") as wav:
        _require(
            (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000),
            "expected-mono-s16le-16000-wav",
        )
        frames = wav.getnframes()
        _require(0 < frames <= 16000 * 15, "input-duration-must-be-within-15-seconds")
        pcm = wav.readframes(frames)
        _require(len(pcm) == frames * 2, "truncated-input-wav")
    ogg = _ffmpeg(
        ffmpeg,
        pcm,
        "-f",
        "s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-i",
        "pipe:0",
        "-c:a",
        "libopus",
        "-b:a",
        "24k",
        "-frame_duration",
        "60",
        "-f",
        "ogg",
        "pipe:1",
    )
    return ogg_opus_packets(ogg), frames


def _offline_socket():
    # Reuse the supplier protocol fixture; do not substitute the S2S input,
    # Doubao adapter, decoder, encoder, playback coordinator or gateway session.
    from backend.tests.test_doubao_realtime import FakeSocket

    class Socket(FakeSocket):
        def __init__(self):
            super().__init__()
            self.uploaded_bytes = 0
            self.uploaded_pcm = bytearray()
            self.append_sizes = []
            self.append_times = []
            self.commit_times = []

        async def send(self, payload):
            event = json.loads(payload)
            await super().send(payload)
            if event["type"] == "input_audio_buffer.append":
                pcm = base64.b64decode(event["audio"])
                self.uploaded_bytes += len(pcm)
                self.uploaded_pcm.extend(pcm)
                self.append_sizes.append(len(pcm))
                self.append_times.append(time.monotonic())
            if event["type"] == "input_audio_buffer.commit":
                self.commit_times.append(time.monotonic())
                self.feed(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "text": "你好",
                    }
                )
                self.feed({"type": "response.output_text.done", "text": "你好，我在。"})
                self.feed(
                    {
                        "type": "response.output_audio.delta",
                        "delta": base64.b64encode(b"\1\0" * 14400).decode(),
                    }
                )
                self.feed({"type": "response.done", "response_id": "offline-response"})

    return Socket()


def _pcm_stats(samples):
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    return {
        "samples": len(samples),
        "rms": round(rms, 3),
        "rms_dbfs": round(20 * math.log10(rms / 32768), 3) if rms else None,
        "peak": max(abs(value) for value in samples),
        "clipped_samples": sum(abs(value) >= 32767 for value in samples),
    }


def _offline_audio_diagnostics(path, supplier, packets, ffmpeg, timestamps):
    """Compare actual supplier-bound bytes without writing any audio or text."""
    from backend.app.audio_formats import IncrementalOggOpusMuxer

    muxer = IncrementalOggOpusMuxer(
        input_sample_rate=16000, frame_duration_ms=60, pre_skip_samples=0
    )
    reference = _ffmpeg(
        ffmpeg,
        b"".join(muxer.add_packet(packet) for packet in packets),
        "-f",
        "ogg",
        "-i",
        "pipe:0",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "s16le",
        "pipe:1",
    )
    actual = bytes(supplier.uploaded_pcm)
    _require(actual == reference, "offline-pcm-differs-from-raw-opus-reference")
    with wave.open(str(path), "rb") as wav:
        original = wav.readframes(wav.getnframes())
    left = struct.unpack(f"<{len(original) // 2}h", original)
    right = struct.unpack(f"<{len(actual) // 2}h", actual)

    def correlation(lag, stride=1):
        paired = list(zip(left[::stride], right[lag : lag + len(left) : stride], strict=True))
        divisor = math.sqrt(sum(x * x for x, _ in paired) * sum(y * y for _, y in paired))
        return sum(x * y for x, y in paired) / divisor if divisor else 0

    # Raw Opus has encoder delay but no pre-skip metadata. Search up to 20ms,
    # rather than silently assuming the original WAV starts at decoded sample 0.
    lag = max(range(min(320, len(right) - len(left)) + 1), key=lambda n: correlation(n, 16))
    return {
        "offline_pcm_matches_raw_reference": True,
        "offline_wav_pcm": _pcm_stats(left),
        "offline_supplier_pcm": _pcm_stats(right),
        "offline_wav_to_supplier_correlation": round(correlation(lag), 6),
        "offline_alignment_delay_samples": lag,
        "offline_append_count": len(supplier.append_sizes),
        "offline_append_bytes_min": min(supplier.append_sizes),
        "offline_append_bytes_max": max(supplier.append_sizes),
        "offline_commit_count": len(supplier.commit_times),
        "offline_commit_after_last_append": supplier.commit_times[0] >= supplier.append_times[-1],
        "offline_pcm_chunks_before_listen_stop": sum(
            at < timestamps["listen_stop"] for at in supplier.append_times
        ),
        "offline_upload_span_ms": round(
            (supplier.append_times[-1] - supplier.append_times[0]) * 1000
        ),
    }


class _NoCascade:
    mock = False  # Advertise the actual Opus wire format; no mock-utf8 negotiation.

    async def open_asr(self):
        raise RuntimeError("unexpected-cascade-call")

    async def open_tts(self, *_args, **_kwargs):
        raise RuntimeError("unexpected-cascade-call")

    async def aclose(self):
        pass


def _send_input(socket, packets, stop, timestamps):
    """Match the physical device cadence while the main thread services ACKs."""
    for packet in packets:
        if stop.is_set():
            break
        socket.send_bytes(packet)
        if stop.wait(0.06):
            break
    timestamps["listen_stop"] = time.monotonic()
    socket.send_json({"type": "listen", "state": "stop"})


def _worker(args, result) -> dict[str, object]:
    from backend.app.config import Settings

    logging.disable(logging.CRITICAL)
    ali = getattr(args, "provider", "doubao") == "aliyun-dialog"
    if ali and args.offline:
        raise ProbeFailure("aliyun-offline-use-protocol-tests")
    options = dict(
        _env_file=ROOT / ".env",
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{(args.worker_directory / 'probe.db').as_posix()}",
        admin_api_key="gateway-probe-local-admin",
        jwt_secret="gateway-probe-local-jwt-secret",
        device_credential_pepper="gateway-probe-local-pepper",
        memory_master_key="gateway-probe-local-memory-key",
        email_otp_secret="gateway-probe-local-email-key",
        email_delivery_mode="development",
        provider_mode="mock",
        fallback_enabled=False,
        provider_timeout_seconds=18,
        web_search_mcp_enabled=False,
        web_search_qwen_enabled=False,
        doubao_realtime_enabled=not ali,
        aliyun_dialog_enabled=ali,
        aliyun_dialog_validated=False,
        doubao_realtime_validated=False,
    )
    if args.offline:
        options["doubao_api_key"] = "offline-not-a-credential"
    settings = Settings(**options)
    packets, frames = _input_packets(args.input_wav, settings.ffmpeg_path)
    result.update(
        {
            "input_wav_samples": frames,
            "input_wav_ms": round(frames / 16),
            "input_opus_packets": len(packets),
            "input_opus_frame_ms": 60,
            "input_opus_sample_rate": 16000,
        }
    )

    with ExitStack() as stack:
        # Some modules instantiate a default app on import. Even that app must
        # receive isolated settings; its real DB or providers are never opened.
        stack.enter_context(patch("backend.app.config.get_settings", return_value=settings))
        from fastapi.testclient import TestClient
        from sqlalchemy import select

        from backend.app.main import create_app
        from backend.app.models import Agent, Device, ModelPreset, ProviderUsage, VoicePreset
        from backend.realtime import aliyun_dialog, doubao
        from backend.tests.conftest import provision_owned_device

        supplier = _offline_socket() if args.offline else None
        original_connect = aliyun_dialog.connect if ali else doubao.connect
        open_attempts = 0

        async def connect_once(*connect_args, **connect_kwargs):
            nonlocal open_attempts
            open_attempts += 1
            result["provider_open_attempts"] = open_attempts
            result["external_provider_called"] = supplier is None
            _require(open_attempts == 1, "more-than-one-provider-session")
            if supplier is not None:
                return supplier
            return await original_connect(*connect_args, **connect_kwargs)

        stack.enter_context(
            patch(f"backend.realtime.{'aliyun_dialog' if ali else 'doubao'}.connect",
                  AsyncMock(side_effect=connect_once))
        )
        client = stack.enter_context(TestClient(create_app(settings)))
        client.app.state.realtime_providers = _NoCascade()
        owned = provision_owned_device(
            client,
            {"X-Admin-Key": settings.admin_api_key},
            serial="HENSUN-SYNTHETIC-PROBE",
            openid="probe-adult",
        )

        async def configure():
            async with client.app.state.session_factory() as db:
                model = await db.get(ModelPreset, "aliyun-dialog" if ali else "doubao-realtime")
                voice = await db.get(VoicePreset, "aliyun-app-default" if ali else "doubao-vv")
                model.enabled = voice.enabled = True
                device = await db.get(Device, owned["device_id"])
                agent = await db.get(Agent, device.active_agent_id)
                agent.model_preset_id, agent.voice_preset_id = model.id, voice.id
                agent.tools_json, agent.memory_consent = "{}", False
                agent.system_prompt = "你是语音接口测试助手。请只用一句简短中文回答，不调用工具。"
                await db.commit()

        client.portal.call(configure)
        with client.websocket_connect(
            "/v1/device/ws",
            headers={
                "Device-Id": owned["serial"],
                "Authorization": f"Bearer {owned['device_secret']}",
            },
        ) as socket:
            socket.send_json(
                {
                    "type": "hello",
                    "version": 1,
                    "audio_params": {
                        "format": "opus",
                        "sample_rate": 16000,
                        "channels": 1,
                        "frame_duration": 60,
                    },
                    "features": {"strict_playback_ack": True, "device_stage_telemetry": True},
                }
            )
            hello = socket.receive_json()
            _require(hello.get("audio_params", {}).get("format") == "opus", "non-opus-hello")
            socket.send_json({"type": "listen", "state": "start", "mode": "manual"})
            input_started_at = time.monotonic()
            timestamps = {}
            stop_upload = threading.Event()
            pool = stack.enter_context(ThreadPoolExecutor(max_workers=1))
            stack.callback(stop_upload.set)
            upload = pool.submit(_send_input, socket, packets, stop_upload, timestamps)
            received = []
            reply = None
            ready = drained = speaker_sent = completed = False
            for _ in range(1200):
                incoming = socket.receive()
                if incoming.get("bytes") is not None:
                    _require(ready and reply is not None, "audio-before-ready")
                    received.append(incoming["bytes"])
                    _require(len(received) <= 750, "response-audio-limit")
                    if not speaker_sent:
                        result["first_gateway_packet_from_input_start_ms"] = round(
                            (time.monotonic() - input_started_at) * 1000
                        )
                        result["first_gateway_packet_after_listen_stop_ms"] = (
                            round((time.monotonic() - timestamps["listen_stop"]) * 1000)
                            if "listen_stop" in timestamps
                            else None
                        )
                        socket.send_json(
                            {
                                "type": "device_stage",
                                "stage": "speaker_pcm_started",
                                "turn_id": reply["turn_id"],
                                "reply_id": reply["reply_id"],
                            }
                        )
                        speaker_sent = True
                    continue
                _require(incoming.get("type") != "websocket.close", "gateway-closed")
                event = json.loads(incoming["text"])
                if event.get("type") in {"error", "alert"}:
                    code = str(event.get("code") or event.get("status") or "turn-rejected")
                    _require(
                        bool(re.fullmatch(r"[a-z][a-z0-9-]{0,79}", code)), "gateway-turn-rejected"
                    )
                    raise ProbeFailure("gateway-" + code)
                if event.get("type") == "listen" and event.get("state") in {"resume", "standby"}:
                    raise ProbeFailure("gateway-listen-" + event["state"])
                if event.get("type") == "mcp":
                    request = event["payload"]
                    socket.send_json(
                        {
                            "type": "mcp",
                            "payload": {
                                "jsonrpc": "2.0",
                                "id": request["id"],
                                "result": {"tools": []},
                            },
                        }
                    )
                if event.get("type") == "tts" and event.get("state") in {"start", "stop"}:
                    state = event["state"]
                    if state == "start":
                        stop_upload.set()  # Cloud endpoint may finish before the WAV ends.
                        _require(reply is None, "more-than-one-reply")
                        reply, ready = event, True
                    else:
                        _require(bool(received), "empty-response-audio")
                        _require(
                            all(event[k] == reply[k] for k in ("turn_id", "reply_id")),
                            "mismatched-playback-stop",
                        )
                        drained = True
                    socket.send_json(
                        {
                            "type": "tts",
                            "state": "ready" if state == "start" else "drained",
                            "turn_id": event["turn_id"],
                            "reply_id": event["reply_id"],
                        }
                    )
                if event.get("type") == "turn" and event.get("state") == "completed":
                    _require(drained, "completed-before-drain")
                    completed = True
                    break
            _require(completed, "turn-incomplete")
            stop_upload.set()
            upload.result(timeout=3)

        async def usage_metadata():
            # Usage is recorded by a bounded background task after drained.
            async with asyncio.timeout(3):
                while True:
                    async with client.app.state.session_factory() as db:
                        rows = list((await db.scalars(select(ProviderUsage))).all())
                    if rows:
                        _require(
                            len(rows) == 1
                            and rows[0].operation == ("managed_dialog" if ali else "realtime_s2s")
                            and rows[0].provider == ("aliyun-dialog" if ali else "doubao")
                            and rows[0].error_code is None,
                            "unexpected-provider-usage",
                        )
                        return {
                            "provider_usage_recorded": True,
                            "provider_cost_status": rows[0].cost_status,
                        }
                    await asyncio.sleep(0.02)

        result.update(client.portal.call(usage_metadata))
        result.update(
            provider_open_attempts=open_attempts,
            external_provider_called=supplier is None,
            completed_turns=1,
            ready_ack=ready,
            drained_ack=drained,
            simulated_speaker_ack_sent=speaker_sent,
            output_opus_packets=len(received),
        )
        if supplier is not None:
            _require(supplier.uploaded_bytes == len(packets) * 960 * 2, "offline-input-sample-loss")
            result["offline_supplier_received_pcm_bytes"] = supplier.uploaded_bytes
            result.update(
                _offline_audio_diagnostics(
                    args.input_wav, supplier, packets, settings.ffmpeg_path, timestamps
                )
            )

    from backend.app.audio_formats import IncrementalOggOpusMuxer

    muxer = IncrementalOggOpusMuxer(
        input_sample_rate=24000, frame_duration_ms=60, pre_skip_samples=0
    )
    decoded = _ffmpeg(
        settings.ffmpeg_path,
        b"".join(muxer.add_packet(p) for p in received),
        "-f",
        "ogg",
        "-i",
        "pipe:0",
        "-ar",
        "24000",
        "-ac",
        "1",
        "-f",
        "s16le",
        "pipe:1",
    )
    _require(len(decoded) == len(received) * 1440 * 2, "output-opus-decode-mismatch")
    result.update(
        output_pcm_sample_rate=24000,
        output_opus_frame_ms=60,
        output_decoded_pcm_bytes=len(decoded),
        status="passed",
    )
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-wav",
        type=Path,
        required=True,
        help="Explicitly authorized mono 16kHz int16 WAV, 0 < duration <= 15s",
    )
    parser.add_argument("--provider", choices=["doubao", "aliyun-dialog"], default="doubao")
    parser.add_argument("--offline", action="store_true", help="No external supplier call")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-directory", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker_directory:
        result = {"provider_open_attempts": 0, "external_provider_called": False}
        try:
            _worker(args, result)
        except Exception as exc:
            # Never emit exception messages: they can contain credentials/audio/text.
            result.update(
                {
                    "status": "failed",
                    "error_code": (
                        exc.code if isinstance(exc, ProbeFailure) else type(exc).__name__
                    ),
                }
            )
        print(json.dumps(result))
        return 0 if result["status"] == "passed" else 1

    from scripts.dual_process_smoke import _stop_owned_processes

    started = time.monotonic()
    result = {
        "provider": args.provider,
        "model": "multimodal-dialog" if args.provider == "aliyun-dialog" else "1.2.6.1",
        "synthetic_device": True,
        "physical_audio_verified": False,
        "transport": "testclient-asgi",
        "external_provider_requested": not args.offline,
        "offline": args.offline,
    }
    output = args.output or ROOT / "run" / "doubao" / f"gateway-probe-{time.time_ns()}.json"
    _require(
        output.resolve().parent == (ROOT / "run" / "doubao").resolve() and output.suffix == ".json",
        "output-must-be-json-in-run-doubao",
    )
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--input-wav",
        str(args.input_wav.resolve()),
        "--provider", args.provider,
    ]
    if args.offline:
        command.append("--offline")
    try:
        with tempfile.TemporaryDirectory(prefix="hensun-doubao-gateway-") as temporary:
            process = subprocess.Popen(
                [*command, "--worker-directory", temporary],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                stdout, _ = process.communicate(timeout=WORKER_TIMEOUT_SECONDS)
                result.update(json.loads(stdout))
            except subprocess.TimeoutExpired:
                result.update(status="failed", error_code="gateway-probe-timeout")
            finally:
                _stop_owned_processes([process])
    except Exception as exc:
        result.update(status="failed", error_code=type(exc).__name__)
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
