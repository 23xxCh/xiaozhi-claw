"""Doubao 3.0 JSON WebSocket adapter, isolated from the device protocol.

Contract: https://docs.volcengine.com/docs/6561/2549778 (2026-09-04).
The caller decodes/reframes device audio and supplies paced 16kHz mono S16LE
PCM. Output is 24kHz mono S16LE PCM; provider completion is not playback ACK.
"""

import asyncio
import base64
import binascii
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect

from backend.realtime.conversation_backend import ConversationEvent, ConversationMessage
from backend.realtime.providers import RealtimeProviderError, RealtimeProviderTimeout

PROVIDER = "doubao-realtime"
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_BUFFERED_AUDIO_BYTES = 2 * 1024 * 1024
USAGE_COUNTERS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "input_text_tokens",
        "input_audio_tokens",
        "cached_text_tokens",
        "cached_audio_tokens",
        "output_text_tokens",
        "output_audio_tokens",
        "text_tokens",
        "audio_tokens",
        "cached_tokens",
    }
)
USAGE_DETAILS = frozenset(
    {
        "input_token_details",
        "output_token_details",
        "input_tokens_details",
        "output_tokens_details",
        "cached_tokens_details",
    }
)
ToolExecutor = Callable[[str, dict[str, object]], Awaitable[str]]


@dataclass(frozen=True)
class DoubaoConfig:
    api_key: str = field(repr=False)
    url: str = "wss://openspeech.bytedance.com/api/v3/duplex/realtime/dialogue"
    model: str = "1.2.6.1"
    voice: str = "zh_female_vv_jupiter_bigtts"
    timeout_seconds: float = 15.0
    close_timeout_seconds: float = 2.0
    queue_max_events: int = 128


class DoubaoRealtimeError(RealtimeProviderError):
    """Stable local codes only: never include upstream messages or credentials."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.retryable = retryable
        super().__init__(PROVIDER, code)


def _safe_usage(value: object, *, nested: bool = False) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, object] = {}
    for key, count in value.items():
        if key in USAGE_COUNTERS and type(count) is int and 0 <= count < 2**63:
            result[key] = count
        elif not nested and key in USAGE_DETAILS:
            detail = _safe_usage(count, nested=True)
            if detail:
                result[key] = detail
    return result


class DoubaoRealtimeBackend:
    def __init__(self, websocket: ClientConnection, config: DoubaoConfig) -> None:
        self.websocket = websocket
        self.config = config
        self.session_id = ""
        self._generation = 0
        self._turn_id = ""
        self._accept_events = False
        self._input_ended = False
        self._muted = True
        self._endpoint = False
        self.endpoint_event = asyncio.Event()
        self._finished = False
        self._closed = False
        self._failure: BaseException | None = None
        self._events: asyncio.Queue[ConversationEvent | BaseException | None] = asyncio.Queue(
            maxsize=config.queue_max_events
        )
        self._buffered_audio_bytes = 0
        self._send_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._waiters: dict[str, asyncio.Future[dict[str, object]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._tool_task: asyncio.Task[None] | None = None
        self._tool_executor: ToolExecutor | None = None
        self._tool_names: set[str] = set()
        self._tool_call_ids: set[str] = set()

    @classmethod
    async def open(
        cls,
        config: DoubaoConfig,
        *,
        instructions: str,
        history: Sequence[ConversationMessage] = (),
        tools: Sequence[dict[str, object]] = (),
        tool_executor: ToolExecutor | None = None,
    ) -> "DoubaoRealtimeBackend":
        parsed = urlsplit(config.url)
        if (
            parsed.scheme != "wss"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not config.api_key.strip()
            or "\n" in config.api_key
            or "\r" in config.api_key
            or not config.model.strip()
            or not config.voice
            or config.timeout_seconds <= 0
            or config.close_timeout_seconds <= 0
            or config.queue_max_events < 2
        ):
            raise DoubaoRealtimeError("invalid-config")
        if len(history) > 20 or len(history) % 2:
            raise DoubaoRealtimeError("invalid-history")
        for index, item in enumerate(history):
            if item.role != ("user" if index % 2 == 0 else "assistant") or not isinstance(
                item.text, str
            ):
                raise DoubaoRealtimeError("invalid-history")
        history = tuple(ConversationMessage(item.role, item.text) for item in history)
        # Reuse the existing registry's OpenAI-style schemas as well as the
        # flat function schema used by Doubao. No tool execution authority is added.
        functions = []
        for tool in tools:
            function = tool.get("function", tool)
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise DoubaoRealtimeError("invalid-tool-schema")
            if not isinstance(function.get("parameters"), dict):
                raise DoubaoRealtimeError("invalid-tool-schema")
            functions.append(
                {
                    "type": "function",
                    "name": function["name"],
                    "description": str(function.get("description", "")),
                    "parameters": function["parameters"],
                }
            )
        if functions and tool_executor is None:
            raise DoubaoRealtimeError("missing-tool-executor")
        try:
            websocket = await connect(
                config.url,
                additional_headers={"X-Api-Key": config.api_key},
                proxy=None,
                open_timeout=config.timeout_seconds,
                close_timeout=config.close_timeout_seconds,
                max_size=MAX_MESSAGE_BYTES,
            )
        except TimeoutError:
            raise RealtimeProviderTimeout(PROVIDER, "connect") from None
        except Exception:
            raise DoubaoRealtimeError("connect-failed", retryable=True) from None
        backend = cls(websocket, config)
        backend._tool_executor = tool_executor
        backend._tool_names = {str(item["name"]) for item in functions}
        backend._reader_task = asyncio.create_task(backend._read_events())
        try:
            created = await backend._request(
                {
                    "type": "session.create",
                    "session": {
                        "model": config.model,
                        "instructions": instructions,
                        "audio": {
                            "input": {"format": {"type": "pcm", "rate": 16000}},
                            "output": {
                                "format": {"type": "pcm", "rate": 24000},
                                "voice": config.voice,
                                "speed": 0,
                                "loudness": 0,
                            },
                        },
                        "tools": functions,
                    },
                    "extension": {"dialog": {"extra": {"strict_audit": True}}},
                },
                "session.created",
            )
            session = created.get("session")
            if (
                not isinstance(session, dict)
                or not isinstance(session.get("id"), str)
                or not session["id"]
            ):
                raise DoubaoRealtimeError("invalid-session-created")
            backend.session_id = session["id"]
            await backend.mute()
            if history:
                await backend._request(
                    {
                        "type": "conversation.item.create",
                        "items": [
                            {
                                "id": uuid4().hex,
                                "type": "message",
                                "role": item.role,
                                "content": [{"type": "input_text", "text": item.text}],
                            }
                            for item in history
                        ],
                    },
                    "conversation.item.added",
                )
            return backend
        except BaseException:
            await backend.close()
            raise

    def begin_turn(self, turn_id: str) -> int:
        if self._turn_id or self._closed or self._failure or not turn_id:
            raise DoubaoRealtimeError("session-not-reusable")
        self._turn_id = turn_id
        self._generation += 1
        self._accept_events = True
        return self._generation

    def endpoint_detected(self) -> bool:
        # The provider's transcription.completed declares end of user speech;
        # this is not an independent local VAD or proof of physical microphone quality.
        return self._endpoint

    def _current(self, generation: int) -> bool:
        return self._accept_events and not self._closed and generation == self._generation

    async def _send(self, event: dict[str, object], *, generation: int | None = None) -> None:
        async with self._send_lock:
            if generation is not None and not self._current(generation):
                return
            event = {"event_id": uuid4().hex, **event}
            try:
                async with asyncio.timeout(self.config.timeout_seconds):
                    await self.websocket.send(json.dumps(event, ensure_ascii=False))
            except TimeoutError:
                raise RealtimeProviderTimeout(PROVIDER, "send") from None
            except Exception:
                raise DoubaoRealtimeError("send-failed", retryable=True) from None

    async def _request(self, event: dict[str, object], expected: str) -> dict[str, object]:
        if self._failure:
            raise self._failure
        future = asyncio.get_running_loop().create_future()
        if expected in self._waiters:
            raise DoubaoRealtimeError("concurrent-control-request")
        self._waiters[expected] = future
        try:
            await self._send(event)
            async with asyncio.timeout(self.config.timeout_seconds):
                return await future
        except TimeoutError:
            raise RealtimeProviderTimeout(PROVIDER, expected) from None
        finally:
            self._waiters.pop(expected, None)
            if not future.done():
                future.cancel()

    async def send_audio(self, pcm: bytes, *, generation: int) -> None:
        if not self._current(generation) or self._input_ended or self._endpoint:
            return
        if not pcm or len(pcm) % 2 or len(pcm) > 64000:
            raise DoubaoRealtimeError("invalid-input-pcm")
        if self._muted:
            await self.unmute()
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            },
            generation=generation,
        )

    async def end_input(self, *, generation: int) -> None:
        if not self._current(generation) or self._input_ended:
            return
        self._input_ended = True
        if self._endpoint or self._finished:
            await self.mute()
            return
        await self._request({"type": "input_audio_buffer.commit"}, "input_audio_buffer.committed")
        await self.mute()

    async def mute(self) -> None:
        await self._send({"type": "input_audio_mute.commit"})
        self._muted = True

    async def unmute(self) -> None:
        await self._send({"type": "input_audio_unmute.commit"})
        self._muted = False

    def _clear_events(self) -> None:
        while not self._events.empty():
            self._events.get_nowait()
        self._buffered_audio_bytes = 0

    def _fail(self, exc: BaseException) -> None:
        if self._failure or self._closed:
            return
        self._failure = exc
        self.endpoint_event.set()
        self._accept_events = False
        self._clear_events()
        self._events.put_nowait(exc)
        for future in self._waiters.values():
            if not future.done():
                future.set_exception(exc)

    def _emit(self, kind: str, source: dict[str, object], **data: object) -> None:
        event = ConversationEvent(
            type=kind,
            generation=self._generation,
            turn_id=self._turn_id,
            question_id=str(source.get("question_id") or source.get("item_id") or ""),
            response_id=str(source.get("response_id") or ""),
            **data,
        )
        if (
            self._events.full()
            or self._buffered_audio_bytes + len(event.audio) > MAX_BUFFERED_AUDIO_BYTES
        ):
            raise DoubaoRealtimeError("event-buffer-full", retryable=True)
        self._buffered_audio_bytes += len(event.audio)
        self._events.put_nowait(event)

    async def _read_events(self) -> None:
        try:
            while True:
                # Once generation is complete, slow device playback must not
                # turn a successful provider response into an idle timeout.
                if self._finished:
                    raw = await self.websocket.recv()
                else:
                    async with asyncio.timeout(self.config.timeout_seconds):
                        raw = await self.websocket.recv()
                if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    raise DoubaoRealtimeError("invalid-json-frame")
                try:
                    event = json.loads(raw)
                except (ValueError, RecursionError):
                    raise DoubaoRealtimeError("invalid-json-frame") from None
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise DoubaoRealtimeError("invalid-event")
                kind = event["type"]
                if kind == "error":
                    raise DoubaoRealtimeError("provider-rejected")
                future = self._waiters.get(kind)
                if future is not None and not future.done():
                    future.set_result(event)
                if kind == "session.closed":
                    if future is None:
                        raise DoubaoRealtimeError("session-closed", retryable=True)
                    return
                if not self._accept_events or self._finished:
                    continue
                if kind == "conversation.item.input_audio_transcription.completed":
                    # Doubao 3.0 completes ASR with `text`; deltas are revisable
                    # hypotheses. Do not concatenate them or assume OpenAI's field.
                    transcript = event.get("text")
                    if not isinstance(transcript, str):
                        raise DoubaoRealtimeError("invalid-transcription-result")
                    self._endpoint = True
                    self.endpoint_event.set()
                    self._emit("transcript_final", event, text=transcript)
                    self._emit("endpoint", event)
                elif kind == "conversation.item.input_audio_transcription.failed":
                    raise DoubaoRealtimeError("transcription-failed")
                elif kind == "conversation.item.input_audio_transcription.delta":
                    self._emit("transcript_delta", event, text=str(event.get("delta") or ""))
                elif kind == "response.output_text.delta":
                    self._emit("text_delta", event, text=str(event.get("delta") or ""))
                elif kind == "response.output_text.done":
                    self._emit("text_final", event, text=str(event.get("text") or ""))
                elif kind == "response.output_audio.delta":
                    encoded = event.get("delta")
                    if not isinstance(encoded, str):
                        raise DoubaoRealtimeError("invalid-audio")
                    try:
                        audio = base64.b64decode(encoded, validate=True)
                    except (ValueError, binascii.Error):
                        raise DoubaoRealtimeError("invalid-audio") from None
                    if not audio or len(audio) % 2:
                        raise DoubaoRealtimeError("invalid-audio")
                    self._emit("audio", event, audio=audio)
                elif kind == "response.output_audio.done":
                    self._emit("audio_done", event)
                elif kind == "response.function_call_arguments.done":
                    if self._tool_task and not self._tool_task.done():
                        raise DoubaoRealtimeError("overlapping-tool-batch")
                    self._tool_task = asyncio.create_task(self._run_tools(event, self._generation))
                elif kind == "response.done":
                    # Current docs name the usage event without fixing its JSON
                    # counters. Accept numeric allowlisted data only; absent != zero.
                    usage = _safe_usage(event.get("usage"))
                    if not usage and isinstance(event.get("response"), dict):
                        usage = _safe_usage(event["response"].get("usage"))
                    if usage:
                        self._emit("usage", event, usage=usage)
                    self._emit("done", event)
                    self._finished = True
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._fail(RealtimeProviderTimeout(PROVIDER, "response-event"))
        except (RealtimeProviderError, RealtimeProviderTimeout) as exc:
            self._fail(exc)
        except Exception:
            if not self._finished:
                self._fail(DoubaoRealtimeError("connection-lost", retryable=True))

    async def _run_tools(self, event: dict[str, object], generation: int) -> None:
        try:
            calls = event.get("items")
            if not isinstance(calls, list) or not 1 <= len(calls) <= 8:
                raise DoubaoRealtimeError("invalid-tool-call")
            results = []
            for call in calls:
                if not self._current(generation):
                    return
                if not isinstance(call, dict):
                    raise DoubaoRealtimeError("invalid-tool-call")
                call_id, name, arguments = (
                    call.get("call_id"),
                    call.get("name"),
                    call.get("arguments"),
                )
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or len(call_id) > 128
                    or call_id in self._tool_call_ids
                    or name not in self._tool_names
                    or not isinstance(arguments, str)
                    or len(arguments) > 16000
                ):
                    raise DoubaoRealtimeError("invalid-tool-call")
                try:
                    parsed = json.loads(arguments)
                except (ValueError, RecursionError):
                    raise DoubaoRealtimeError("invalid-tool-arguments") from None
                if not isinstance(parsed, dict) or self._tool_executor is None:
                    raise DoubaoRealtimeError("invalid-tool-arguments")
                self._tool_call_ids.add(call_id)
                try:
                    async with asyncio.timeout(self.config.timeout_seconds):
                        output = await self._tool_executor(name, parsed)
                    if not isinstance(output, str) or len(output) > 16000:
                        output = '{"error":"invalid-tool-result"}'
                except Exception:
                    output = '{"error":"tool-execution-failed"}'
                results.append(
                    {
                        "call_id": call_id,
                        "role": "tool",
                        "content": [{"type": "input_text", "text": output}],
                    }
                )
            await self._send(
                {"type": "conversation.item.create", "items": results}, generation=generation
            )
        except asyncio.CancelledError:
            raise
        except (RealtimeProviderError, RealtimeProviderTimeout) as exc:
            self._fail(exc)
        except Exception:
            self._fail(DoubaoRealtimeError("tool-processing-failed"))

    async def events(self) -> AsyncIterator[ConversationEvent]:
        while True:
            item = await self._events.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            self._buffered_audio_bytes -= len(item.audio)
            if not self._current(item.generation):
                continue
            yield item
            if item.type == "done":
                return

    def invalidate(self, *, generation: int) -> None:
        """Stop local audio/tool work immediately, without waiting for the network."""
        if self._closed or generation != self._generation:
            return
        self._accept_events = False
        self.endpoint_event.set()
        self._clear_events()
        self._events.put_nowait(None)
        if self._tool_task:
            self._tool_task.cancel()

    async def cancel(self, *, generation: int) -> None:
        # A prior synchronous invalidate must not prevent the wire cancellation.
        if self._closed or generation != self._generation:
            return
        self.invalidate(generation=generation)
        self._generation += 1
        if not self._failure:
            if not self._finished:
                await self._request({"type": "response.cancel"}, "response.canceled")
            await self.mute()

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self.invalidate(generation=self._generation)
            self._generation += 1
            try:
                if not self._failure and self._reader_task and not self._reader_task.done():
                    async with asyncio.timeout(self.config.close_timeout_seconds):
                        await self._request({"type": "session.close"}, "session.closed")
            except (TimeoutError, RealtimeProviderError, RealtimeProviderTimeout):
                pass
            finally:
                self._closed = True
                try:
                    async with asyncio.timeout(self.config.close_timeout_seconds):
                        await self.websocket.close()
                except Exception:
                    pass
                tasks = [task for task in (self._tool_task, self._reader_task) if task]
                for task in tasks:
                    task.cancel()
                if tasks:
                    done, pending = await asyncio.wait(
                        tasks, timeout=self.config.close_timeout_seconds
                    )
                    for task in done:
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            task.result()
                    for task in pending:
                        task.cancel()
                self._clear_events()
                self._events.put_nowait(None)
