"""Exercise cascade TTS failures with no network or real audio devices."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.app.device_connections import DeviceConnectionManager
from backend.app.models import ConversationSession, ProviderUsage, UsageEvent
from backend.realtime import session as realtime_session
from backend.realtime.playback import PlaybackCoordinator
from backend.realtime.providers import MockAsrSession, RealtimeProviderTimeout

from .conftest import provision_owned_device

REPLY = "第一句完整说完。第二句也要说完。"


class FaultyTts:
    def __init__(self, mode, *, encoded):
        self.mode = mode
        self.encoded = encoded
        self.calls = []
        self.started_at = None
        self.cancelled = False
        self.packet_delivered = asyncio.Event()

    async def synthesize(self, text):
        self.calls.append(text)
        self.started_at = self.started_at or time.perf_counter()
        if self.mode == "timeout":
            # Long enough to distinguish the configured 50 ms first-audio deadline,
            # bounded independently so the test also terminates before the fix.
            await asyncio.sleep(0.3)
            raise RealtimeProviderTimeout("qwen-tts", "response-event")
        if self.mode == "empty":
            yield b""
            return
        if self.mode == "queued-prefix":
            if len(self.calls) == 1:
                yield b"primary-buffered-pcm"
            return
        if self.mode == "partial":
            for _ in range(5 if self.encoded else 1):
                yield b"primary-already-audible"
            if self.encoded:
                await asyncio.wait_for(self.packet_delivered.wait(), 1)
            raise RealtimeProviderTimeout("qwen-tts", "response-event")
        raise AssertionError("unexpected failure mode")

    async def finish(self):
        pass

    async def cancel(self):
        self.cancelled = True


class FixedReply:
    async def reply_stream(self, _request, **_kwargs):
        yield REPLY


class CascadeProviders:
    def __init__(self, tts, *, encoded):
        self.mock = not encoded
        self.tts = tts
        self.llm = FixedReply()

    async def open_asr(self):
        return MockAsrSession()

    async def open_tts(self, *_args):
        return self.tts

    async def aclose(self):
        pass


class FallbackSpeech:
    def __init__(self):
        self.calls = []
        self.started_at = None

    async def synthesize(self, text):
        self.started_at = self.started_at or time.perf_counter()
        self.calls.append(text)
        return [text.encode()]


def encoder_factory(tts, instances):
    class BufferedEncoder:
        def __init__(self, _path):
            self.queue = asyncio.Queue()
            self.cancelled = False
            instances.append(self)

        async def start(self):
            pass

        async def write(self, pcm):
            self.queue.put_nowait(pcm)

        async def packets(self, *, prebuffer_packets=5):
            buffered = []
            ended = False
            while len(buffered) < prebuffer_packets:
                item = await self.queue.get()
                if item is None:
                    ended = True
                    break
                buffered.append(item)
            for item in buffered:
                yield item
                tts.packet_delivered.set()
            if ended:
                return
            while (item := await self.queue.get()) is not None:
                yield item
                tts.packet_delivered.set()

        async def finish(self):
            self.queue.put_nowait(None)

        async def cancel(self):
            self.cancelled = True
            # Simulate cancellation that waits for a subprocess. The caller must
            # stop the packet pump first or this flush can release stale PCM.
            self.queue.put_nowait(None)
            await asyncio.sleep(0)

    return BufferedEncoder


def exercise_turn(client, admin_headers, monkeypatch, *, mode, encoded):
    tts = FaultyTts(mode, encoded=encoded)
    fallback = FallbackSpeech()
    client.app.state.settings.provider_timeout_seconds = 0.05
    client.app.state.realtime_providers = CascadeProviders(tts, encoded=encoded)
    client.app.state.fallback_providers = SimpleNamespace(speech=fallback)
    encoders = []
    monkeypatch.setattr(realtime_session, "StreamingPcmToOpus", encoder_factory(tts, encoders))
    owned = provision_owned_device(client, admin_headers)
    messages, audio = [], []
    with client.websocket_connect(
        "/v1/device/ws",
        headers={
            "Device-Id": owned["serial"],
            "Authorization": f"Bearer {owned['device_secret']}",
        },
    ) as socket:
        socket.send_json({"type": "hello", "version": 1, "features": {"strict_playback_ack": True}})
        assert socket.receive_json()["type"] == "hello"
        socket.send_json({"type": "listen", "state": "start"})
        socket.send_bytes("讲两句话".encode())
        socket.send_json({"type": "listen", "state": "stop"})
        terminal = False
        for _ in range(40):
            frame = socket.receive()
            if frame.get("bytes") is not None:
                audio.append(frame["bytes"])
                continue
            payload = json.loads(frame["text"])
            messages.append(payload)
            if payload["type"] == "tts" and payload.get("state") in {"start", "stop"}:
                socket.send_json(
                    {
                        **payload,
                        "state": "ready" if payload["state"] == "start" else "drained",
                    }
                )
            if payload["type"] == "error" or (
                payload["type"] == "turn" and payload.get("state") == "completed"
            ):
                if not terminal:
                    socket.send_json({"type": "ping", "sequence": 1})
                    terminal = True
            if payload["type"] == "pong":
                break
        else:
            pytest.fail("voice turn did not finish")
    return SimpleNamespace(
        tts=tts,
        fallback=fallback,
        audio=audio,
        messages=messages,
        encoders=encoders,
    )


@pytest.mark.parametrize("encoded", [False, True], ids=["direct", "encoded"])
@pytest.mark.parametrize("mode", ["timeout", "empty"])
def test_before_first_audio_failure_uses_backup_without_losing_or_repeating_text(
    client,
    admin_headers,
    monkeypatch,
    mode,
    encoded,
):
    result = exercise_turn(client, admin_headers, monkeypatch, mode=mode, encoded=encoded)
    assert result.fallback.calls
    assert "".join(result.fallback.calls) == REPLY
    assert b"".join(result.audio).decode() == REPLY
    assert sum(m.get("state") == "completed" for m in result.messages) == 1
    assert not any(m["type"] == "error" for m in result.messages)
    assert result.tts.cancelled
    if mode == "timeout":
        assert result.fallback.started_at - result.tts.started_at < 0.2

    async def usage():
        async with client.app.state.session_factory() as db:
            rows = list((await db.scalars(select(ProviderUsage))).all())
            conversation = await db.scalar(select(ConversationSession))
            usage_event = await db.scalar(select(UsageEvent))
            return rows, conversation, usage_event

    rows, conversation, usage_event = client.portal.call(usage)
    (row,) = [item for item in rows if item.operation == "tts"]
    assert row.provider == "dashscope-batch"
    assert row.model == client.app.state.settings.fallback_tts_model
    assert row.error_code == "fallback-batch"
    assert row.cost_status == "unknown" and row.cost_micros is None
    (attempt,) = [item for item in rows if item.operation == "tts_attempt"]
    assert attempt.provider == "dashscope"
    assert attempt.model == "qwen3-tts-flash-realtime"
    assert attempt.error_code == "fallback-primary-failed"
    assert attempt.cost_status == "unknown" and attempt.cost_micros is None
    assert attempt.input_units == attempt.output_units == 0
    known_subtotal = sum(item.cost_micros for item in rows if item.cost_micros is not None)
    assert conversation.provider_cost_micros == usage_event.provider_cost_micros == known_subtotal


@pytest.mark.parametrize("encoded", [False, True], ids=["direct", "encoded"])
def test_failure_after_audio_was_sent_never_replays_whole_sentence(
    client,
    admin_headers,
    monkeypatch,
    encoded,
):
    result = exercise_turn(client, admin_headers, monkeypatch, mode="partial", encoded=encoded)
    assert result.audio
    assert all(packet == b"primary-already-audible" for packet in result.audio)
    assert result.fallback.calls == []
    assert any(m["type"] == "error" for m in result.messages)
    assert not any(m.get("state") == "completed" for m in result.messages)
    assert result.tts.cancelled


def test_failure_with_only_queued_pcm_replays_unheard_prefix_once_and_discards_old_audio(
    client,
    admin_headers,
    monkeypatch,
):
    result = exercise_turn(client, admin_headers, monkeypatch, mode="queued-prefix", encoded=True)
    assert result.tts.calls == ["第一句完整说完。", "第二句也要说完。"]
    assert result.fallback.calls == [REPLY]
    assert b"".join(result.audio).decode() == REPLY
    assert all(encoder.cancelled for encoder in result.encoders)
    assert sum(m.get("state") == "completed" for m in result.messages) == 1


@pytest.mark.parametrize("mode", ["timeout", "empty", "partial"])
async def test_fixed_policy_prompt_uses_same_first_audio_fallback_boundary(monkeypatch, mode):
    tts = FaultyTts(mode, encoded=True)
    fallback = FallbackSpeech()
    manager = DeviceConnectionManager()
    playback = PlaybackCoordinator(ready_timeout_seconds=0.1, strict_drain_timeout_seconds=0.1)
    playback.configure(strict_ack=True)
    audio = []
    messages = []

    async def send_json(payload):
        messages.append(payload)
        if payload.get("type") == "tts" and payload.get("state") in {"start", "stop"}:
            playback.acknowledge(
                "ready" if payload["state"] == "start" else "drained",
                payload["reply_id"],
                payload["turn_id"],
            )

    async def send_bytes(packet):
        audio.append(packet)

    socket = SimpleNamespace(
        send_json=send_json,
        send_bytes=send_bytes,
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(provider_timeout_seconds=0.05, ffmpeg_path="fake"),
                device_connections=manager,
                realtime_providers=CascadeProviders(tts, encoded=True),
                fallback_providers=SimpleNamespace(speech=fallback),
            )
        ),
    )
    monkeypatch.setattr(realtime_session, "StreamingPcmToOpus", encoder_factory(tts, []))
    lease = await manager.connect("policy-device", socket, "policy-session")
    message = "已经聊了一会儿，起来活动一下吧。"
    await asyncio.wait_for(
        realtime_session._speak_fixed_message(
            socket,
            lease,
            "Cherry",
            1.0,
            message,
            playback,
        ),
        2,
    )
    if mode == "partial":
        assert fallback.calls == []
        assert audio and all(packet == b"primary-already-audible" for packet in audio)
    else:
        assert fallback.calls == [message]
        assert b"".join(audio).decode() == message
    assert tts.cancelled
    assert playback.reply_id is None
    assert [m["state"] for m in messages if m.get("state") in {"start", "stop"}] == [
        "start",
        "stop",
    ]


def test_volc_failure_does_not_switch_to_ali(client, admin_headers, monkeypatch):
    from dataclasses import replace

    original = realtime_session._load_snapshot

    async def volc_snapshot(*args):
        return replace(await original(*args), tts_provider="volc-tts", tts_model="seed-tts-2.0")

    monkeypatch.setattr(realtime_session, "_load_snapshot", volc_snapshot)
    result = exercise_turn(client, admin_headers, monkeypatch, mode="timeout", encoded=True)
    assert result.fallback.calls == []
    assert result.audio == []
    assert any(m["type"] == "error" for m in result.messages)
    assert result.tts.cancelled
