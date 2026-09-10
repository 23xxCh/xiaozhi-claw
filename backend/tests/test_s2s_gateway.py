"""Local S2S protocol tests; fake codecs and supplier are not acoustic acceptance."""

import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.device_connections import DeviceConnectionManager
from backend.app.models import (
    Agent,
    ConversationSession,
    Device,
    ModelPreset,
    ProviderUsage,
    UsageEvent,
)
from backend.realtime import s2s
from backend.realtime import session as realtime_session
from backend.realtime.conversation_backend import ConversationEvent
from backend.realtime.playback import PlaybackCoordinator
from backend.realtime.providers import MockLlmProvider, RealtimeProviderTimeout
from backend.realtime.s2s import SpeechToSpeechInput, process_s2s_turn, record_s2s_usage
from backend.realtime.session import VoiceTurnTimeline

from .conftest import provision_owned_device


class FakeDecoder:
    def __init__(self, _path=""):
        self.queue = asyncio.Queue()
        self.cancelled = False

    async def start(self):
        pass

    async def write(self, _packet):
        self.queue.put_nowait(b"\0\0" * 320)

    async def chunks(self):
        while (chunk := await self.queue.get()) is not None:
            yield chunk

    async def finish(self):
        self.queue.put_nowait(None)

    async def cancel(self):
        self.cancelled = True
        self.queue.put_nowait(None)


class FakeEncoder(FakeDecoder):
    async def write(self, pcm):
        self.queue.put_nowait(b"fake-opus:" + pcm)

    async def packets(self, **_kwargs):
        async for packet in self.chunks():
            yield packet


class FakeBackend:
    def __init__(self, *, complete_on_commit=False):
        self.config = SimpleNamespace(timeout_seconds=1)
        self.session_id = "supplier-session"
        self.endpoint_event = asyncio.Event()
        self.queue = asyncio.Queue()
        self.consumed = asyncio.Queue()
        self.complete_on_commit = complete_on_commit
        self.closed = False
        self.cancelled = False
        self.uploaded = []
        self.uploaded_event = asyncio.Event()
        self.end_calls = 0
        self.invalidated = False

    def begin_turn(self, turn_id):
        self.turn_id = turn_id
        return 1

    def emit(self, kind, **kwargs):
        self.queue.put_nowait(ConversationEvent(kind, 1, self.turn_id, **kwargs))

    def endpoint_detected(self):
        return self.endpoint_event.is_set()

    async def playback_completed(self):
        return

    async def send_audio(self, pcm, **_kwargs):
        self.uploaded.append(pcm)
        self.uploaded_event.set()

    async def end_input(self, **_kwargs):
        self.end_calls += 1
        if self.complete_on_commit:
            self.emit("audio", audio=b"\1\0" * 480)
            self.emit("transcript_final", text="你好")
            self.emit("text_final", text="你好，我在。")
            self.emit("done", response_id="response-1")

    async def events(self):
        while (event := await self.queue.get()) is not None:
            yield event
            self.consumed.put_nowait(event.type)

    def invalidate(self, **_kwargs):
        self.invalidated = True

    async def cancel(self, **_kwargs):
        self.cancelled = True

    async def close(self):
        self.closed = True
        self.queue.put_nowait(None)


class FakeDeviceSocket:
    def __init__(self, app, playback):
        self.app = app
        self.playback = playback
        self.messages = []
        self.packets = []
        self.outgoing = asyncio.Queue()
        self.auto_ready = True
        self.auto_drained = True
        self.closed = False

    async def send_json(self, payload):
        self.messages.append(payload)
        self.outgoing.put_nowait(payload)
        if payload.get("type") == "tts":
            state = payload.get("state")
            if (state == "start" and self.auto_ready) or (state == "stop" and self.auto_drained):
                self.playback.acknowledge(
                    "ready" if state == "start" else "drained",
                    payload["reply_id"],
                    payload["turn_id"],
                )

    async def send_bytes(self, payload):
        self.packets.append(payload)
        self.outgoing.put_nowait(payload)

    async def close(self, **_kwargs):
        self.closed = True

    async def until(self, kind, state=None):
        async with asyncio.timeout(2):
            while True:
                payload = await self.outgoing.get()
                if isinstance(payload, dict) and payload.get("type") == kind:
                    if state is None or payload.get("state") == state:
                        return payload


@pytest.fixture
async def turn(client, admin_headers, monkeypatch):
    owned = provision_owned_device(client, admin_headers)
    factory = client.app.state.session_factory
    async with factory() as db:
        device = await db.get(Device, owned["device_id"])
        conversation = ConversationSession(
            user_id=device.owner_user_id,
            device_id=device.id,
            agent_id=device.active_agent_id,
            usage_profile_id=device.active_profile_id,
        )
        db.add(conversation)
        await db.commit()
    playback = PlaybackCoordinator(ready_timeout_seconds=0.5, strict_drain_timeout_seconds=0.1)
    playback.configure(strict_ack=True)
    socket = FakeDeviceSocket(client.app, playback)
    manager = DeviceConnectionManager()
    socket.app = SimpleNamespace(
        state=SimpleNamespace(
            settings=client.app.state.settings,
            session_factory=factory,
            device_connections=manager,
        )
    )
    lease = await manager.connect(owned["serial"], socket, "device-session")
    backend = FakeBackend()
    source = SpeechToSpeechInput(backend, FakeDecoder(), "turn-1")
    monkeypatch.setattr(s2s, "StreamingPcmToOpus", FakeEncoder)
    history = []
    telemetry = set()
    timeline = VoiceTurnTimeline("turn-1", time.perf_counter())
    authorize = AsyncMock()
    kwargs = dict(
        device_id=device.id,
        user_id=device.owner_user_id,
        conversation_id=conversation.id,
        snapshot=SimpleNamespace(realtime_model="1.2.6.1",
                                 realtime_provider="doubao", route_kind="realtime_s2s"),
        history=history,
        timeline=timeline,
        playback=playback,
        user_exit_event=asyncio.Event(),
        authorize=authorize,
        telemetry_tasks=telemetry,
    )
    tasks = []

    def start():
        task = asyncio.create_task(process_s2s_turn(socket, lease, source, **kwargs))
        tasks.append(task)
        return task

    async def settle():
        await asyncio.gather(*telemetry)

    yield SimpleNamespace(
        socket=socket,
        backend=backend,
        source=source,
        playback=playback,
        start=start,
        history=history,
        timeline=timeline,
        settle=settle,
        factory=factory,
        kwargs=kwargs,
    )
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await source.cancel()
    await settle()


def emit_reply(turn, *, done=True):
    turn.backend.emit("transcript_final", text="你好")
    turn.backend.emit("text_final", text="你好，我在。")
    turn.backend.emit("audio", audio=b"\1\0" * 480)
    if done:
        turn.backend.emit("done", response_id="response-1")


async def usage_rows(turn):
    await turn.settle()
    async with turn.factory() as db:
        return list((await db.scalars(select(ProviderUsage))).all())


async def test_s2s_requires_ready_then_drained_before_counting_success(turn):
    turn.socket.auto_ready = turn.socket.auto_drained = False
    task = turn.start()
    emit_reply(turn)
    start = await turn.socket.until("tts", "start")
    assert turn.socket.packets == []
    assert not turn.playback.acknowledge("ready", start["reply_id"], "stale-turn")
    assert turn.playback.acknowledge("ready", start["reply_id"], "turn-1")
    stop = await turn.socket.until("tts", "stop")
    assert turn.socket.packets
    assert not task.done()
    assert not any(m.get("type") == "turn" for m in turn.socket.messages)
    assert turn.playback.acknowledge("drained", stop["reply_id"], "turn-1")
    assert await asyncio.wait_for(task, 2) is False
    assert len(turn.history) == 2
    assert turn.timeline.elapsed_ms("device_speaker_started") is None
    (row,) = await usage_rows(turn)
    assert row.cost_status == "unknown" and row.cost_micros is None
    assert row.error_code is None
    assert json.loads(row.usage_details_json)["provider_usage"] == {}


@pytest.mark.parametrize("keep_events_flowing", [False, True])
async def test_no_audio_has_absolute_deadline_and_cleans_up(turn, monkeypatch, keep_events_flowing):
    monkeypatch.setattr(s2s, "FIRST_AUDIO_TIMEOUT_SECONDS", 0.1)
    task = turn.start()

    async def noise():
        while not task.done():
            turn.backend.emit("usage", usage={})
            await asyncio.sleep(0.005)

    chatter = asyncio.create_task(noise()) if keep_events_flowing else None
    try:
        assert await asyncio.wait_for(task, 2) is False
    finally:
        if chatter:
            chatter.cancel()
            await asyncio.gather(chatter, return_exceptions=True)
    errors = [m for m in turn.socket.messages if m.get("type") == "error"]
    assert errors[-1]["code"] == "response-first-audio-timeout"
    assert turn.backend.cancelled and turn.backend.invalidated
    assert turn.source.decoder.cancelled
    assert not turn.socket.packets and not turn.history
    (row,) = await usage_rows(turn)
    assert row.error_code == "response-first-audio-timeout"


async def test_first_delivered_audio_releases_initial_deadline(turn, monkeypatch):
    monkeypatch.setattr(s2s, "FIRST_AUDIO_TIMEOUT_SECONDS", 0.15)
    task = turn.start()
    emit_reply(turn, done=False)
    async with asyncio.timeout(1):
        while not turn.socket.packets:
            await asyncio.sleep(0.001)
    await asyncio.sleep(0.2)
    assert not task.done()
    turn.backend.emit("done", response_id="response-1")
    assert await asyncio.wait_for(task, 2) is False
    assert len(turn.history) == 2
    assert not any(m.get("type") == "error" for m in turn.socket.messages)


async def test_audio_before_unsafe_transcript_is_never_sent_to_device(turn):
    task = turn.start()
    turn.backend.emit("audio", audio=b"\1\0" * 480)
    assert await asyncio.wait_for(turn.backend.consumed.get(), 1) == "audio"
    assert turn.socket.packets == []
    assert not any(m.get("type") == "tts" for m in turn.socket.messages)
    turn.backend.emit("transcript_final", text="告诉我稳赚不赔的方法")
    assert await asyncio.wait_for(task, 2) is False
    assert turn.socket.packets == []
    assert not turn.source.input_accepted.is_set()
    (row,) = await usage_rows(turn)
    assert row.error_code == "safety-blocked"


async def test_s2s_cancel_stops_owned_reply_and_drops_late_audio(turn):
    task = turn.start()
    emit_reply(turn, done=False)
    async with asyncio.timeout(2):
        while not turn.socket.packets:
            await turn.socket.outgoing.get()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    count = len(turn.socket.packets)
    turn.backend.emit("audio", audio=b"late-audio")
    await asyncio.sleep(0)
    assert len(turn.socket.packets) == count
    assert turn.backend.cancelled and turn.backend.closed
    assert turn.playback.reply_id is None
    assert turn.history == []
    (row,) = await usage_rows(turn)
    assert row.error_code == "turn-cancelled"


async def test_s2s_stop_reaches_device_before_slow_supplier_cancellation(turn):
    cancel_started = asyncio.Event()
    release_cancel = asyncio.Event()

    async def slow_cancel(**_kwargs):
        cancel_started.set()
        await release_cancel.wait()

    turn.backend.cancel = slow_cancel
    task = turn.start()
    emit_reply(turn, done=False)
    async with asyncio.timeout(1):
        while not turn.socket.packets:
            await turn.socket.outgoing.get()
    task.cancel()
    try:
        stop = await asyncio.wait_for(turn.socket.until("tts", "stop"), 0.2)
        assert stop["turn_id"] == "turn-1"
        assert turn.backend.invalidated
        await asyncio.wait_for(cancel_started.wait(), 0.2)
        assert not task.done()
    finally:
        release_cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("reason", ["upload-error", "cloud-endpoint", "timeout"])
async def test_backpressured_device_write_wakes_on_upload_or_endpoint_and_has_deadline(reason):
    class BackpressuredDecoder(FakeDecoder):
        def __init__(self):
            super().__init__()
            self.write_cancelled = asyncio.Event()

        async def write(self, packet):
            await super().write(packet)
            try:
                await asyncio.Event().wait()
            finally:
                self.write_cancelled.set()

    backend = FakeBackend()
    backend.config.timeout_seconds = 0.03

    async def upload(_pcm, **_kwargs):
        if reason == "upload-error":
            raise RuntimeError("upload-failed")
        if reason == "cloud-endpoint":
            backend.endpoint_event.set()
        else:
            await asyncio.Event().wait()

    backend.send_audio = upload
    decoder = BackpressuredDecoder()
    source = SpeechToSpeechInput(backend, decoder, "blocked-write")
    try:
        if reason == "upload-error":
            with pytest.raises(RuntimeError, match="upload-failed"):
                await asyncio.wait_for(source.send_audio(b"opus"), 0.2)
        elif reason == "timeout":
            with pytest.raises(RealtimeProviderTimeout, match="input-write"):
                await asyncio.wait_for(source.send_audio(b"opus"), 0.2)
        else:
            await asyncio.wait_for(source.send_audio(b"opus"), 0.2)
            await source.finish_input()
            await source.finish_input()
            assert backend.end_calls == 1
        assert decoder.write_cancelled.is_set()
    finally:
        await source.cancel()


async def test_supplier_eof_without_response_done_is_not_success(turn):
    task = turn.start()
    emit_reply(turn, done=False)
    turn.backend.queue.put_nowait(None)
    assert await asyncio.wait_for(task, 2) is False
    assert turn.history == []
    (row,) = await usage_rows(turn)
    assert row.error_code == "empty-response"


async def test_strict_missing_drain_does_not_count_completed_turn(turn):
    turn.socket.auto_drained = False
    task = turn.start()
    emit_reply(turn)
    assert await asyncio.wait_for(task, 2) is False
    assert turn.history == []
    (row,) = await usage_rows(turn)
    assert row.error_code == "tts-drained-timeout"


async def test_s2s_usage_is_unknown_and_duplicate_delivery_is_idempotent(turn):
    arguments = dict(
        conversation_id=turn.kwargs["conversation_id"],
        user_id=turn.kwargs["user_id"],
        device_id=turn.kwargs["device_id"],
        model="1.2.6.1",
        provider_session_id="supplier-session",
        response_id="response-1",
        turn_id="turn-1",
        usage={"input_tokens": 20, "output_tokens": 30},
        completed=True,
        latency_ms=123,
        error_code=None,
    )
    await asyncio.gather(*(record_s2s_usage(turn.factory, **arguments) for _ in range(2)))
    (row,) = await usage_rows(turn)
    assert row.cost_micros is None and row.cost_status == "unknown"
    assert row.provider_request_id == "response-1"
    assert json.loads(row.usage_details_json)["provider_usage"] == arguments["usage"]
    async with turn.factory() as db:
        conversation = await db.get(ConversationSession, arguments["conversation_id"])
        events = list((await db.scalars(select(UsageEvent))).all())
    assert conversation.turn_count == 1
    assert len(events) == 1 and events[0].quantity == 1


class ForbiddenCascade:
    mock = True

    async def open_asr(self):
        raise AssertionError("S2S must not invoke cascade ASR")

    async def open_tts(self, *_args, **_kwargs):
        raise AssertionError("S2S must not invoke cascade TTS")

    async def aclose(self):
        pass


def configure_s2s(client, owned):
    async def configure():
        async with client.app.state.session_factory() as db:
            model = await db.get(ModelPreset, "doubao-realtime")
            model.enabled = True
            device = await db.get(Device, owned["device_id"])
            agent = await db.get(Agent, device.active_agent_id)
            agent.model_preset_id = model.id
            agent.voice_preset_id = "doubao-vv"
            await db.commit()

    client.portal.call(configure)
    client.app.state.realtime_providers = ForbiddenCascade()


@pytest.mark.parametrize("enabled,key", [(False, "test-key"), (True, "")])
def test_enabled_catalog_still_requires_runtime_gate_and_key(
    client,
    admin_headers,
    monkeypatch,
    enabled,
    key,
):
    owned = provision_owned_device(client, admin_headers)
    configure_s2s(client, owned)
    client.app.state.settings.doubao_realtime_enabled = enabled
    client.app.state.settings.doubao_api_key = key
    open_backend = AsyncMock(side_effect=AssertionError("closed gate must not open supplier"))
    monkeypatch.setattr(realtime_session.DoubaoRealtimeBackend, "open", open_backend)
    with client.websocket_connect(
        "/v1/device/ws",
        headers={
            "Device-Id": owned["serial"],
            "Authorization": f"Bearer {owned['device_secret']}",
        },
    ) as socket:
        socket.send_json({"type": "hello", "version": 1})
        assert socket.receive_json()["type"] == "hello"
        socket.send_json({"type": "listen", "state": "start"})
        assert socket.receive_json()["code"] == "s2s-not-enabled"
    open_backend.assert_not_awaited()


def test_enabled_s2s_gateway_dispatch_keeps_cascade_unused(client, admin_headers, monkeypatch):
    owned = provision_owned_device(client, admin_headers)
    configure_s2s(client, owned)
    client.app.state.settings.doubao_realtime_enabled = True
    client.app.state.settings.doubao_api_key = "test-key-not-a-credential"
    backend = FakeBackend(complete_on_commit=True)
    open_backend = AsyncMock(return_value=backend)
    monkeypatch.setattr(realtime_session.DoubaoRealtimeBackend, "open", open_backend)
    monkeypatch.setattr(realtime_session, "StreamingOpusToPcm", FakeDecoder)
    monkeypatch.setattr(s2s, "StreamingPcmToOpus", FakeEncoder)
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
        socket.send_bytes(b"test-device-opus")
        socket.send_json({"type": "listen", "state": "stop"})
        messages, packets = receive_one_turn(socket)
    open_backend.assert_awaited_once()
    assert [m["state"] for m in messages if m.get("state") in {"start", "stop"}] == [
        "start",
        "stop",
    ]
    assert len(packets) == 1
    assert backend.uploaded == [b"\0\0" * 320]
    assert backend.closed


def receive_one_turn(socket, *, on_start=None):
    messages, packets = [], []
    for _ in range(30):
        message = socket.receive()
        if "bytes" in message:
            packets.append(message["bytes"])
            continue
        payload = json.loads(message["text"])
        messages.append(payload)
        assert payload["type"] != "error", payload
        if payload["type"] == "tts" and payload.get("state") in {"start", "stop"}:
            if payload["state"] == "start" and on_start:
                on_start()
            socket.send_json(
                {
                    "type": "tts",
                    "state": "ready" if payload["state"] == "start" else "drained",
                    "reply_id": payload["reply_id"],
                    "turn_id": payload["turn_id"],
                }
            )
        if payload["type"] == "turn" and payload.get("state") == "completed":
            break
    else:
        pytest.fail("turn did not complete")
    # The receive loop handles drained before ping, so this flushes any duplicate
    # completion and waits for the same connection to become available again.
    socket.send_json({"type": "ping", "sequence": 1})
    assert socket.receive_json()["type"] == "pong"
    return messages, packets


class CapturingMockLlm(MockLlmProvider):
    def __init__(self):
        self.contexts = []

    async def reply_stream(self, request, **kwargs):
        self.contexts.append([dict(message) for message in request.context.messages])
        async for text in super().reply_stream(request, **kwargs):
            yield text


@pytest.mark.parametrize("ending", ["abort", "disconnect"])
def test_cancel_during_input_records_supplier_attempt_without_completed_quota(
    client,
    admin_headers,
    monkeypatch,
    ending,
):
    owned = provision_owned_device(client, admin_headers)
    configure_s2s(client, owned)
    client.app.state.settings.doubao_realtime_enabled = True
    client.app.state.settings.doubao_api_key = "test-key-not-a-credential"
    backend = FakeBackend()
    monkeypatch.setattr(
        realtime_session.DoubaoRealtimeBackend, "open", AsyncMock(return_value=backend)
    )
    monkeypatch.setattr(realtime_session, "StreamingOpusToPcm", FakeDecoder)
    with client.websocket_connect(
        "/v1/device/ws",
        headers={
            "Device-Id": owned["serial"],
            "Authorization": f"Bearer {owned['device_secret']}",
        },
    ) as socket:
        socket.send_json({"type": "listen", "state": "start"})
        socket.send_bytes(b"input-before-vad")
        client.portal.call(asyncio.wait_for, backend.uploaded_event.wait(), 2)
        if ending == "abort":
            socket.send_json({"type": "abort"})
            assert socket.receive_json()["state"] == "aborted"
    assert backend.cancelled and backend.closed

    async def recorded():
        async with client.app.state.session_factory() as db:
            rows = list((await db.scalars(select(ProviderUsage))).all())
            completed = list((await db.scalars(select(UsageEvent))).all())
            return rows, completed

    rows, completed = client.portal.call(recorded)
    assert len(rows) == 1 and not completed
    assert rows[0].operation == "realtime_s2s"
    assert rows[0].error_code == "input-cancelled"
    assert rows[0].cost_micros is None and rows[0].cost_status == "unknown"


def test_same_socket_switches_cascade_s2s_cascade_with_short_term_history(
    client,
    admin_headers,
    monkeypatch,
):
    owned = provision_owned_device(client, admin_headers)
    llm = CapturingMockLlm()
    client.app.state.realtime_providers = replace(client.app.state.realtime_providers, llm=llm)
    client.app.state.settings.doubao_realtime_enabled = True
    client.app.state.settings.doubao_api_key = "test-key-not-a-credential"
    backend = FakeBackend(complete_on_commit=True)
    open_backend = AsyncMock(return_value=backend)
    monkeypatch.setattr(realtime_session.DoubaoRealtimeBackend, "open", open_backend)
    monkeypatch.setattr(realtime_session, "StreamingOpusToPcm", FakeDecoder)
    monkeypatch.setattr(s2s, "StreamingPcmToOpus", FakeEncoder)

    async def enable_catalog():
        async with client.app.state.session_factory() as db:
            model = await db.get(ModelPreset, "doubao-realtime")
            model.enabled = True
            device = await db.get(Device, owned["device_id"])
            await db.commit()
            return device.active_agent_id

    agent_id = client.portal.call(enable_catalog)
    headers = {"Authorization": f"Bearer {owned['user_token']}"}
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
        socket.send_bytes("我喜欢蓝色".encode())
        socket.send_json({"type": "listen", "state": "stop"})
        _, first_audio = receive_one_turn(socket)
        first_reply = b"".join(first_audio).decode()
        changed = client.patch(
            f"/v1/agents/{agent_id}",
            headers=headers,
            json={
                "model_preset_id": "doubao-realtime",
                "voice_preset_id": "doubao-vv",
            },
        )
        assert changed.status_code == 200, changed.text
        socket.send_json({"type": "listen", "state": "start"})
        socket.send_bytes(b"second-turn-opus")
        socket.send_json({"type": "listen", "state": "stop"})
        receive_one_turn(socket)
        assert [(m.role, m.text) for m in open_backend.call_args.kwargs["history"]] == [
            ("user", "我喜欢蓝色"),
            ("assistant", first_reply),
        ]
        changed = client.patch(
            f"/v1/agents/{agent_id}",
            headers=headers,
            json={
                "model_preset_id": "fast-chat",
                "voice_preset_id": "cherry",
            },
        )
        assert changed.status_code == 200, changed.text
        socket.send_json({"type": "listen", "state": "start"})
        socket.send_bytes("我喜欢什么颜色".encode())
        socket.send_json({"type": "listen", "state": "stop"})
        receive_one_turn(socket)
    assert len(llm.contexts) == 2
    conversational = [
        (m["role"], m["content"]) for m in llm.contexts[-1] if m["role"] in {"user", "assistant"}
    ]
    assert conversational == [
        ("user", "我喜欢蓝色"),
        ("assistant", first_reply),
        ("user", "你好"),
        ("assistant", "你好，我在。"),
        ("user", "我喜欢什么颜色"),
    ]


def test_cloud_endpoint_without_more_device_frames_and_late_stop(
    client,
    admin_headers,
    monkeypatch,
):
    owned = provision_owned_device(client, admin_headers)
    configure_s2s(client, owned)
    client.app.state.settings.doubao_realtime_enabled = True
    client.app.state.settings.doubao_api_key = "test-key-not-a-credential"
    backend = FakeBackend()
    open_backend = AsyncMock(return_value=backend)
    monkeypatch.setattr(realtime_session.DoubaoRealtimeBackend, "open", open_backend)
    monkeypatch.setattr(realtime_session, "StreamingOpusToPcm", FakeDecoder)
    monkeypatch.setattr(s2s, "StreamingPcmToOpus", FakeEncoder)

    async def cloud_finishes_input():
        await asyncio.wait_for(backend.uploaded_event.wait(), 2)
        backend.emit("transcript_final", text="你好")
        backend.emit("text_final", text="你好，我在。")
        backend.emit("audio", audio=b"\1\0" * 480)
        backend.emit("done", response_id="response-1")
        backend.endpoint_event.set()

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
        socket.send_bytes(b"only-device-frame")
        client.portal.call(cloud_finishes_input)
        # No device stop or extra audio triggers processing; the supplier event does.
        _, packets = receive_one_turn(
            socket,
            on_start=lambda: socket.send_json(
                {
                    "type": "listen",
                    "state": "stop",
                }
            ),
        )
        assert packets
        socket.send_json({"type": "listen", "state": "stop"})
        socket.send_json({"type": "ping", "sequence": 2})
        assert socket.receive_json()["type"] == "pong"
    open_backend.assert_awaited_once()
    assert backend.end_calls == 1

async def test_timeout_outcome_survives_disconnect_during_cleanup(turn, monkeypatch, caplog):
    monkeypatch.setattr(s2s, "FIRST_AUDIO_TIMEOUT_SECONDS", 0.01)
    closing = asyncio.Event()

    async def slow_close():
        closing.set()
        await asyncio.Event().wait()

    turn.backend.close = slow_close
    caplog.set_level("INFO", logger="uvicorn.error")
    task = turn.start()
    await asyncio.wait_for(closing.wait(), 1)
    task.cancel()  # Device closes its socket immediately after receiving the error.
    with pytest.raises(asyncio.CancelledError):
        await task
    (row,) = await usage_rows(turn)
    assert row.error_code == "response-first-audio-timeout"
    assert any('"error_code":"response-first-audio-timeout"' in r.message
               for r in caplog.records if "voice turn outcome" in r.message)
