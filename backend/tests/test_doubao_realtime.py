import asyncio
import base64
import json

import pytest

from backend.realtime import doubao
from backend.realtime.conversation_backend import ConversationMessage
from backend.realtime.doubao import DoubaoConfig, DoubaoRealtimeBackend, DoubaoRealtimeError
from backend.realtime.providers import RealtimeProviderTimeout


class FakeSocket:
    def __init__(self, *, create_ack: bool = True, close_ack: bool = True) -> None:
        self.received: asyncio.Queue[str | bytes | BaseException] = asyncio.Queue()
        self.sent: list[dict[str, object]] = []
        self.create_ack = create_ack
        self.close_ack = close_ack
        self.close_calls = 0
        self.recv_count = 0

    def feed(self, *events: dict[str, object]) -> None:
        for event in events:
            self.received.put_nowait(json.dumps(event))

    async def send(self, raw: str) -> None:
        event = json.loads(raw)
        self.sent.append(event)
        kind = event["type"]
        if kind == "session.create" and self.create_ack:
            self.feed({"type": "session.created", "session": {"id": "mock-dialog"}})
        elif kind == "conversation.item.create":
            self.feed({"type": "conversation.item.added", "items": event["items"]})
        elif kind == "input_audio_buffer.commit":
            self.feed({"type": "input_audio_buffer.committed"})
        elif kind == "response.cancel":
            self.feed({"type": "response.canceled"})
        elif kind == "session.close" and self.close_ack:
            self.feed({"type": "session.closed"})

    async def recv(self) -> str | bytes:
        raw = await self.received.get()
        self.recv_count += 1
        if isinstance(raw, BaseException):
            raise raw
        return raw

    async def close(self) -> None:
        self.close_calls += 1


async def open_backend(monkeypatch, socket=None, **kwargs):
    socket = socket or FakeSocket()
    config = kwargs.pop("config", DoubaoConfig(api_key="fake-not-a-real-key"))
    captured = {}

    async def fake_connect(url, **options):
        captured.update({"url": url, **options})
        return socket

    monkeypatch.setattr(doubao, "connect", fake_connect)
    backend = await DoubaoRealtimeBackend.open(config, instructions="你是桌面助手。", **kwargs)
    return backend, socket, captured


def audio_event(pcm=b"\x01\x00" * 480, **kwargs):
    return {
        "type": "response.output_audio.delta",
        "delta": base64.b64encode(pcm).decode(),
        "question_id": "question-one",
        "response_id": "response-one",
        **kwargs,
    }


@pytest.mark.asyncio
async def test_open_uses_3_0_schema_and_explicit_paired_history(monkeypatch):
    history = [ConversationMessage("user", "你好"), ConversationMessage("assistant", "你好呀")]
    backend, socket, captured = await open_backend(monkeypatch, history=history)
    try:
        assert captured["additional_headers"] == {"X-Api-Key": "fake-not-a-real-key"}
        assert captured["proxy"] is None
        assert captured["max_size"] == doubao.MAX_MESSAGE_BYTES
        create = socket.sent[0]
        assert create["session"]["model"] == "1.2.6.1"
        assert "id" not in create["session"]
        assert create["session"]["audio"] == {
            "input": {"format": {"type": "pcm", "rate": 16000}},
            "output": {
                "format": {"type": "pcm", "rate": 24000},
                "voice": "zh_female_vv_jupiter_bigtts",
                "speed": 0,
                "loudness": 0,
            },
        }
        assert create["extension"]["dialog"]["extra"]["strict_audit"] is True
        assert socket.sent[1]["type"] == "input_audio_mute.commit"
        items = socket.sent[2]["items"]
        assert [item["role"] for item in items] == ["user", "assistant"]
        assert items[0]["content"] == [{"type": "input_text", "text": "你好"}]
        assert backend.session_id == "mock-dialog"
        assert "fake-not-a-real-key" not in repr(backend.config)
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "history",
    [
        [ConversationMessage("user", "incomplete")],
        [ConversationMessage("assistant", "wrong"), ConversationMessage("user", "order")],
        [ConversationMessage("user", "q"), ConversationMessage("assistant", "a")] * 11,
    ],
)
async def test_invalid_history_fails_before_connect(monkeypatch, history):
    async def forbidden(*args, **kwargs):
        raise AssertionError("must validate before opening a connection")

    monkeypatch.setattr(doubao, "connect", forbidden)
    with pytest.raises(DoubaoRealtimeError, match="invalid-history"):
        await DoubaoRealtimeBackend.open(
            DoubaoConfig(api_key="fake"), instructions="", history=history
        )


@pytest.mark.asyncio
async def test_input_pcm_commit_and_mute_do_not_finish_the_response(monkeypatch):
    backend, socket, _ = await open_backend(monkeypatch)
    try:
        generation = backend.begin_turn("turn-1")
        pcm = b"\x02\x00" * 320
        await backend.send_audio(pcm, generation=generation)
        await backend.end_input(generation=generation)
        sent = socket.sent[2:]
        assert [item["type"] for item in sent] == [
            "input_audio_unmute.commit",
            "input_audio_buffer.append",
            "input_audio_buffer.commit",
            "input_audio_mute.commit",
        ]
        assert base64.b64decode(sent[1]["audio"]) == pcm
        await backend.send_audio(pcm, generation=generation)
        assert len(socket.sent) == 6
        with pytest.raises(DoubaoRealtimeError, match="session-not-reusable"):
            backend.begin_turn("turn-2")
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_audio_done_waits_for_response_done_and_usage_is_allowlisted(monkeypatch):
    backend, socket, _ = await open_backend(monkeypatch)
    generation = backend.begin_turn("turn-1")
    events = []

    async def collect():
        async for event in backend.events():
            events.append(event)

    consumer = asyncio.create_task(collect())
    try:
        socket.feed(
            {"type": "conversation.item.input_audio_transcription.delta", "delta": "你"},
            {"type": "conversation.item.input_audio_transcription.completed", "text": "你好"},
            {"type": "response.output_text.delta", "delta": "你好呀"},
            audio_event(),
            {"type": "response.output_audio.done"},
        )
        await asyncio.sleep(0.01)
        assert backend.endpoint_detected() is True
        assert consumer.done() is False
        socket.feed(
            {
                "type": "response.done",
                "usage": {
                    "input_tokens": 12,
                    "output_audio_tokens": 20,
                    "total_tokens": True,
                    "input_token_details": {"cached_tokens": 2, "text": "PRIVATE"},
                    "text": "PRIVATE",
                    "audio": "PRIVATE",
                    "api_key": "PRIVATE",
                    "cached_audio_tokens": -1,
                    "output_tokens": "99",
                },
                "response": {"transcript": "PRIVATE"},
            }
        )
        await asyncio.wait_for(consumer, 1)
        assert [event.type for event in events] == [
            "transcript_delta",
            "transcript_final",
            "endpoint",
            "text_delta",
            "audio",
            "audio_done",
            "usage",
            "done",
        ]
        assert all(event.generation == generation for event in events)
        assert events[-2].usage == {
            "input_tokens": 12,
            "output_audio_tokens": 20,
            "input_token_details": {"cached_tokens": 2},
        }
        assert events[4].audio == b"\x01\x00" * 480
        assert "PRIVATE" not in repr(events)
    finally:
        await backend.close()
        await consumer


@pytest.mark.asyncio
async def test_missing_usage_stays_unknown_and_slow_playback_does_not_timeout(monkeypatch):
    backend, socket, _ = await open_backend(
        monkeypatch, config=DoubaoConfig(api_key="fake", timeout_seconds=0.02)
    )
    try:
        backend.begin_turn("turn-1")
        socket.feed(
            audio_event(), {"type": "response.output_audio.done"}, {"type": "response.done"}
        )
        await asyncio.sleep(0.05)
        events = [event async for event in backend.events()]
        assert [event.type for event in events] == ["audio", "audio_done", "done"]
        assert all(event.usage is None for event in events)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_cancel_drops_queued_and_late_audio_and_old_generation_uploads(monkeypatch):
    backend, socket, _ = await open_backend(monkeypatch)
    try:
        generation = backend.begin_turn("turn-1")
        socket.feed(audio_event())
        await asyncio.sleep(0)
        await backend.cancel(generation=generation)
        before = len(socket.sent)
        socket.feed(audio_event(), {"type": "response.done"})
        await backend.send_audio(b"\x00\x00" * 320, generation=generation)
        await backend.end_input(generation=generation)
        assert [event async for event in backend.events()] == []
        assert len(socket.sent) == before
        assert [item["type"] for item in socket.sent[-2:]] == [
            "response.cancel",
            "input_audio_mute.commit",
        ]
    finally:
        await backend.close()


async def test_synchronous_invalidate_still_allows_one_wire_cancel(monkeypatch):
    backend, socket, _ = await open_backend(monkeypatch)
    try:
        generation = backend.begin_turn("turn-invalidate")
        backend.invalidate(generation=generation)
        assert backend.endpoint_event.is_set()
        assert [event async for event in backend.events()] == []
        sent_before = len(socket.sent)
        await backend.send_audio(b"\0\0" * 320, generation=generation)
        assert len(socket.sent) == sent_before
        await backend.cancel(generation=generation)
        await backend.cancel(generation=generation)
        assert sum(event["type"] == "response.cancel" for event in socket.sent) == 1
    finally:
        await backend.close()


async def test_completed_generation_closes_without_cancelling_finished_response(monkeypatch):
    backend, socket, _ = await open_backend(monkeypatch)
    generation = backend.begin_turn("turn-done")
    socket.feed({"type": "response.done", "response_id": "response-done"})
    assert [event.type async for event in backend.events()] == ["done"]
    backend.invalidate(generation=generation)
    await backend.cancel(generation=generation)
    await backend.close()
    assert not any(event["type"] == "response.cancel" for event in socket.sent)
    assert socket.close_calls == 1


@pytest.mark.asyncio
async def test_reader_fails_fast_when_consumer_exceeds_bounded_queue(monkeypatch):
    backend, socket, _ = await open_backend(
        monkeypatch, config=DoubaoConfig(api_key="fake", queue_max_events=2)
    )
    try:
        backend.begin_turn("turn-1")
        socket.feed(audio_event(), audio_event(), audio_event())
        await asyncio.sleep(0)
        with pytest.raises(DoubaoRealtimeError, match="event-buffer-full"):
            _ = [event async for event in backend.events()]
        assert backend._events.qsize() <= 2
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        {"type": "response.output_audio.delta", "delta": "not valid base64!"},
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"odd").decode()},
        {"type": "error", "error": {"code": "PRIVATE", "message": "PRIVATE"}},
        {"type": "conversation.item.input_audio_transcription.failed", "error": "PRIVATE"},
    ],
)
async def test_invalid_audio_and_upstream_errors_are_typed_without_payload(monkeypatch, event):
    backend, socket, _ = await open_backend(monkeypatch)
    try:
        backend.begin_turn("turn-1")
        socket.feed(event)
        with pytest.raises(DoubaoRealtimeError) as raised:
            _ = [item async for item in backend.events()]
        assert raised.value.provider == "doubao-realtime"
        assert "PRIVATE" not in str(raised.value)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_open_timeout_closes_socket_and_reader(monkeypatch):
    socket = FakeSocket(create_ack=False)
    with pytest.raises(RealtimeProviderTimeout):
        await open_backend(
            monkeypatch,
            socket,
            config=DoubaoConfig(api_key="fake", timeout_seconds=0.01, close_timeout_seconds=0.01),
        )
    assert socket.close_calls == 1


@pytest.mark.asyncio
async def test_close_is_idempotent_and_bounded_without_session_closed_ack(monkeypatch):
    backend, socket, _ = await open_backend(
        monkeypatch,
        FakeSocket(close_ack=False),
        config=DoubaoConfig(api_key="fake", timeout_seconds=1, close_timeout_seconds=0.01),
    )
    await asyncio.wait_for(backend.close(), 0.2)
    await backend.close()
    assert socket.close_calls == 1
    assert backend._reader_task.done()


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "读取当前时间",
            "parameters": {"type": "object"},
        },
    }
]


@pytest.mark.asyncio
async def test_real_asr_shape_uses_completed_text_not_revisable_delta_history(monkeypatch):
    backend, socket, _ = await open_backend(monkeypatch)
    try:
        backend.begin_turn("turn-1")
        for hypothesis in ("你", "你好啊", "你好", "你好，请说欢迎语"):
            socket.feed({"type": "conversation.item.input_audio_transcription.delta",
                         "delta": hypothesis})
        socket.feed({"type": "conversation.item.input_audio_transcription.completed",
                     "text": "你好，请说一句欢迎语。"}, {"type": "response.done"})
        events = [event async for event in backend.events()]
        assert [event.text for event in events if event.type == "transcript_final"] == [
            "你好，请说一句欢迎语。"
        ]
        assert sum(event.type == "endpoint" for event in events) == 1
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, 123, {"private": "text"}])
async def test_invalid_completed_text_is_a_protocol_error_not_empty_audio(monkeypatch, value):
    backend, socket, _ = await open_backend(monkeypatch)
    try:
        backend.begin_turn("turn-1")
        socket.feed({"type": "conversation.item.input_audio_transcription.completed",
                     "text": value})
        with pytest.raises(DoubaoRealtimeError, match="invalid-transcription-result"):
            _ = [event async for event in backend.events()]
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_tools_reuse_executor_and_return_all_call_ids_without_blocking_reader(monkeypatch):
    called = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def execute(name, arguments):
        called.append((name, arguments))
        entered.set()
        await release.wait()
        return '{"time":"12:00"}'

    backend, socket, _ = await open_backend(monkeypatch, tools=TOOLS, tool_executor=execute)
    try:
        backend.begin_turn("turn-1")
        assert socket.sent[0]["session"]["tools"][0]["name"] == "get_time"
        socket.feed(
            {
                "type": "response.function_call_arguments.done",
                "items": [
                    {"call_id": "call-1", "name": "get_time", "arguments": '{"zone":"CN"}'},
                    {"call_id": "call-2", "name": "get_time", "arguments": "{}"},
                ],
            }
        )
        await asyncio.wait_for(entered.wait(), 1)
        # Reader keeps processing while the tool callback is awaiting local work.
        socket.feed(
            {"type": "conversation.item.input_audio_transcription.completed", "text": "时间"}
        )
        await asyncio.sleep(0)
        assert backend.endpoint_detected()
        release.set()
        await asyncio.wait_for(backend._tool_task, 1)
        result = socket.sent[-1]
        assert result["type"] == "conversation.item.create"
        assert [item["call_id"] for item in result["items"]] == ["call-1", "call-2"]
        assert all(item["role"] == "tool" for item in result["items"])
        assert len(called) == 2
    finally:
        release.set()
        await backend.close()


@pytest.mark.asyncio
async def test_cancel_stops_tool_results_from_crossing_generation(monkeypatch):
    entered = asyncio.Event()

    async def execute(name, arguments):
        entered.set()
        await asyncio.Event().wait()
        return "never"

    backend, socket, _ = await open_backend(monkeypatch, tools=TOOLS, tool_executor=execute)
    try:
        generation = backend.begin_turn("turn-1")
        socket.feed(
            {
                "type": "response.function_call_arguments.done",
                "items": [
                    {"call_id": "call-1", "name": "get_time", "arguments": "{}"},
                ],
            }
        )
        await asyncio.wait_for(entered.wait(), 1)
        await backend.cancel(generation=generation)
        assert not any(item["type"] == "conversation.item.create" for item in socket.sent)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_model_enablement_belongs_to_catalog_not_the_protocol(monkeypatch):
    backend, socket, _ = await open_backend(
        monkeypatch, config=DoubaoConfig(api_key="fake", model="future-approved-model")
    )
    try:
        assert socket.sent[0]["session"]["model"] == "future-approved-model"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_late_listen_stop_does_not_commit_after_server_endpoint(monkeypatch):
    backend, socket, _ = await open_backend(monkeypatch)
    try:
        generation = backend.begin_turn("turn-1")
        socket.feed(
            {"type": "conversation.item.input_audio_transcription.completed", "text": "你好"}
        )
        await asyncio.wait_for(backend.endpoint_event.wait(), 1)
        await backend.end_input(generation=generation)
        assert backend.endpoint_detected()
        assert not any(item["type"] == "input_audio_buffer.commit" for item in socket.sent)
        assert socket.sent[-1]["type"] == "input_audio_mute.commit"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_reader_error_wakes_endpoint_waiter_without_claiming_asr_completed(monkeypatch):
    backend, socket, _ = await open_backend(monkeypatch)
    try:
        backend.begin_turn("turn-1")
        socket.feed({"type": "error", "message": "PRIVATE"})
        await asyncio.wait_for(backend.endpoint_event.wait(), 1)
        assert backend.endpoint_detected() is False
        with pytest.raises(DoubaoRealtimeError):
            _ = [item async for item in backend.events()]
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_peer_disconnect_after_done_does_not_erase_buffered_audio(monkeypatch):
    backend, socket, _ = await open_backend(monkeypatch)
    try:
        backend.begin_turn("turn-1")
        socket.feed(audio_event(), {"type": "response.done"})
        socket.received.put_nowait(ConnectionError("private upstream details"))
        await asyncio.sleep(0)
        assert [event.type async for event in backend.events()] == ["audio", "done"]
    finally:
        await backend.close()
