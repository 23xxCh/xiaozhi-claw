import asyncio
import base64
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

import httpx
from websockets.asyncio.client import ClientConnection, connect

from backend.app.audio_formats import IncrementalOggOpusMuxer
from backend.app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    emotion: str = "neutral"


class RealtimeProviderError(RuntimeError):
    def __init__(self, provider: str, code: str) -> None:
        self.provider = provider
        self.code = code[:80] or "unknown"
        super().__init__(f"{provider} realtime provider error: {self.code}")


def _raise_if_provider_error(event: dict[str, object], provider: str) -> None:
    event_type = str(event.get("type") or "")
    if event_type != "error" and not event_type.endswith("_error"):
        return
    detail = event.get("error")
    if isinstance(detail, dict):
        code = str(detail.get("code") or detail.get("type") or event_type)
    elif isinstance(detail, str):
        code = str(event.get("code") or event_type)
    else:
        code = str(event.get("code") or event_type or "unknown")
    raise RealtimeProviderError(provider, code)


class RealtimeAsrSession(Protocol):
    async def send_audio(self, frame: bytes) -> None: ...

    def endpoint_detected(self) -> bool: ...

    async def finish(self) -> TranscriptionResult: ...

    async def cancel(self) -> None: ...


class RealtimeLlmProvider(Protocol):
    async def reply_stream(
        self,
        transcript: str,
        history: list[dict[str, str]],
        memories: list[str],
        *,
        system_prompt: str,
        model: str,
        temperature: float,
        tools: list[dict[str, object]] | None = None,
        tool_executor: Callable[[str, dict[str, object]], Awaitable[str]] | None = None,
    ) -> AsyncIterator[str]: ...


class RealtimeTtsSession(Protocol):
    async def synthesize(self, text: str) -> AsyncIterator[bytes]: ...

    async def finish(self) -> None: ...

    async def cancel(self) -> None: ...


class MockAsrSession:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    async def send_audio(self, frame: bytes) -> None:
        self.frames.append(frame)

    def endpoint_detected(self) -> bool:
        return False

    async def finish(self) -> TranscriptionResult:
        return TranscriptionResult(b"".join(self.frames).decode(errors="replace"), "neutral")

    async def cancel(self) -> None:
        self.frames.clear()


class MockLlmProvider:
    async def reply_stream(
        self,
        transcript: str,
        history: list[dict[str, str]],
        memories: list[str],
        *,
        system_prompt: str,
        model: str,
        temperature: float,
        tools: list[dict[str, object]] | None = None,
        tool_executor: Callable[[str, dict[str, object]], Awaitable[str]] | None = None,
    ) -> AsyncIterator[str]:
        del history, memories, system_prompt, model, temperature, tools, tool_executor
        yield f"收到：{transcript}"


class MockTtsSession:
    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        yield text.encode()

    async def finish(self) -> None:
        return None

    async def cancel(self) -> None:
        return None


class QwenRealtimeAsrSession:
    def __init__(self, websocket: ClientConnection) -> None:
        self.websocket = websocket
        self.closed = False
        self.ogg_muxer = IncrementalOggOpusMuxer(
            input_sample_rate=16000, frame_duration_ms=60
        )
        self._endpoint = asyncio.Event()
        self._events: asyncio.Queue[dict[str, object] | BaseException] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None

    @classmethod
    async def open(cls, settings: Settings) -> "QwenRealtimeAsrSession":
        url = (
            f"{settings.qwen_realtime_asr_url.rstrip('/')}?model={settings.qwen_realtime_asr_model}"
        )
        websocket = await connect(
            url,
            proxy=None,
            additional_headers={
                "Authorization": f"Bearer {settings.asr_api_key}",
                "OpenAI-Beta": "realtime=v1",
            },
            open_timeout=settings.provider_timeout_seconds,
            max_size=2 * 1024 * 1024,
        )
        session = cls(websocket)
        await websocket.send(
            json.dumps(
                {
                    "event_id": f"event_{uuid.uuid4().hex}",
                    "type": "session.update",
                    "session": {
                        "input_audio_format": "opus",
                        "sample_rate": 16000,
                        "input_audio_transcription": {"language": "zh"},
                        "turn_detection": None,
                    },
                }
            )
        )
        await session._wait_for("session.updated")
        session._reader_task = asyncio.create_task(session._read_events())
        return session

    async def _read_events(self) -> None:
        try:
            while True:
                event = json.loads(await self.websocket.recv())
                _raise_if_provider_error(event, "qwen-asr")
                if event.get("type") == "input_audio_buffer.speech_stopped":
                    self._endpoint.set()
                await self._events.put(event)
                if event.get("type") == "session.finished":
                    return
        except BaseException as exc:
            # Wake the gateway immediately so finish() can surface the stable
            # provider error and use its bounded batch fallback.
            self._endpoint.set()
            await self._events.put(exc)

    def endpoint_detected(self) -> bool:
        return self._endpoint.is_set()

    async def _wait_for(self, expected: str) -> dict[str, object]:
        async with asyncio.timeout(15):
            while True:
                event = json.loads(await self.websocket.recv())
                _raise_if_provider_error(event, "qwen-asr")
                if event.get("type") == expected:
                    return event

    async def send_audio(self, frame: bytes) -> None:
        ogg = self.ogg_muxer.add_packet(frame)
        await self.websocket.send(
            json.dumps(
                {
                    "event_id": f"event_{uuid.uuid4().hex}",
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(ogg).decode("ascii"),
                }
            )
        )

    async def finish(self) -> TranscriptionResult:
        if not self._endpoint.is_set():
            # Manual mode requires an explicit commit. The device local VAD
            # usually sends listen.stop first; commit then session.finish is
            # the official non-VAD close sequence.
            await self.websocket.send(
                json.dumps(
                    {
                        "event_id": f"event_{uuid.uuid4().hex}",
                        "type": "input_audio_buffer.commit",
                    }
                )
            )
        await self.websocket.send(
            json.dumps({"event_id": f"event_{uuid.uuid4().hex}", "type": "session.finish"})
        )
        text = ""
        emotion = "neutral"
        async with asyncio.timeout(30):
            while True:
                event = await self._events.get()
                if isinstance(event, BaseException):
                    raise event
                event_type = event.get("type")
                if event_type in {
                    "conversation.item.input_audio_transcription.text",
                    "conversation.item.input_audio_transcription.completed",
                }:
                    text = str(event.get("transcript") or event.get("text") or text)
                    emotion = str(event.get("emotion") or emotion)
                if event_type == "session.finished":
                    break
        await self.websocket.close()
        if self._reader_task is not None:
            await self._reader_task
        self.closed = True
        return TranscriptionResult(text.strip(), emotion)

    async def cancel(self) -> None:
        if not self.closed:
            await self.websocket.close()
            if self._reader_task is not None:
                self._reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reader_task
            self.closed = True


class DeepSeekStreamingLlmProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def reply_stream(
        self,
        transcript: str,
        history: list[dict[str, str]],
        memories: list[str],
        *,
        system_prompt: str,
        model: str,
        temperature: float,
        tools: list[dict[str, object]] | None = None,
        tool_executor: Callable[[str, dict[str, object]], Awaitable[str]] | None = None,
    ) -> AsyncIterator[str]:
        messages: list[dict[str, object]] = [{"role": "system", "content": system_prompt}]
        if memories:
            messages.append(
                {
                    "role": "system",
                    "content": "用户主动授权保存的摘要记忆：\n"
                    + "\n".join(f"- {item}" for item in memories[:10]),
                }
            )
        messages.extend(history[-10:])
        messages.append({"role": "user", "content": transcript})
        base_url = self.settings.llm_url.rstrip("/")
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        tool_result_added = False
        for _ in range(3):
            request: dict[str, object] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": True,
                # Voice turns are short and latency-sensitive. DeepSeek V4
                # enables thinking by default, which can add several seconds
                # before the first audible sentence for simple questions.
                "thinking": {"type": "disabled"},
            }
            if tools:
                request["tools"] = tools
                if tool_result_added:
                    request["tool_choice"] = "none"
            tool_calls: dict[int, dict[str, str]] = {}
            assistant_parts: list[str] = []
            reasoning_parts: list[str] = []
            async with httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json=request,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            break
                        payload = json.loads(data)
                        delta = payload.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content") if isinstance(delta, dict) else None
                        if content:
                            assistant_parts.append(str(content))
                            yield str(content)
                        reasoning = (
                            delta.get("reasoning_content")
                            if isinstance(delta, dict)
                            else None
                        )
                        if reasoning:
                            reasoning_parts.append(str(reasoning))
                        calls = delta.get("tool_calls") if isinstance(delta, dict) else None
                        if isinstance(calls, list):
                            for call in calls:
                                if not isinstance(call, dict):
                                    continue
                                index = int(call.get("index", 0))
                                entry = tool_calls.setdefault(
                                    index, {"id": "", "name": "", "arguments": ""}
                                )
                                if call.get("id"):
                                    entry["id"] += str(call["id"])
                                function = call.get("function")
                                if isinstance(function, dict):
                                    entry["name"] += str(function.get("name") or "")
                                    entry["arguments"] += str(function.get("arguments") or "")
            if not tool_calls or tool_executor is None:
                return
            assistant_message: dict[str, object] = {
                "role": "assistant",
                "content": "".join(assistant_parts) or "",
                "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments"],
                            },
                        }
                        for call in tool_calls.values()
                    ],
            }
            if reasoning_parts:
                assistant_message["reasoning_content"] = "".join(reasoning_parts)
            messages.append(assistant_message)
            for call in tool_calls.values():
                try:
                    arguments = json.loads(call["arguments"] or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError("arguments must be an object")
                    result = await tool_executor(call["name"], arguments)
                except Exception as exc:
                    logger.info("tool call failed: %s", type(exc).__name__)
                    result = "工具暂时不可用，请稍后再试。"
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": result}
                )
            tool_result_added = True
        return


class QwenRealtimeTtsSession:
    def __init__(
        self,
        websocket: ClientConnection,
        *,
        event_timeout_seconds: float = 30.0,
    ) -> None:
        self.websocket = websocket
        self.event_timeout_seconds = event_timeout_seconds
        self.closed = False

    @classmethod
    async def open(
        cls, settings: Settings, *, voice: str, speech_rate: float
    ) -> "QwenRealtimeTtsSession":
        url = (
            f"{settings.qwen_realtime_tts_url.rstrip('/')}?model={settings.qwen_realtime_tts_model}"
        )
        websocket = await connect(
            url,
            proxy=None,
            additional_headers={
                "Authorization": f"Bearer {settings.tts_api_key}",
                "OpenAI-Beta": "realtime=v1",
            },
            open_timeout=settings.provider_timeout_seconds,
            max_size=2 * 1024 * 1024,
        )
        session = cls(
            websocket,
            event_timeout_seconds=settings.provider_timeout_seconds,
        )
        await websocket.send(
            json.dumps(
                {
                    "event_id": f"event_{uuid.uuid4().hex}",
                    "type": "session.update",
                    "session": {
                        "voice": voice,
                        "mode": "commit",
                        "language_type": "Chinese",
                        "response_format": "pcm",
                        "sample_rate": 24000,
                        "speech_rate": speech_rate,
                    },
                }
            )
        )
        await session._wait_for("session.updated")
        return session

    async def _wait_for(self, expected: str) -> dict[str, object]:
        async with asyncio.timeout(15):
            while True:
                event = json.loads(await self.websocket.recv())
                _raise_if_provider_error(event, "qwen-tts")
                if event.get("type") == expected:
                    return event

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        await self.websocket.send(
            json.dumps(
                {
                    "event_id": f"event_{uuid.uuid4().hex}",
                    "type": "input_text_buffer.append",
                    "text": text,
                }
            )
        )
        await self.websocket.send(
            json.dumps(
                {"event_id": f"event_{uuid.uuid4().hex}", "type": "input_text_buffer.commit"}
            )
        )
        events: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()

        async def receive_response() -> None:
            try:
                while True:
                    # Drain provider events independently from FFmpeg and the
                    # device's real-time playback pace. Otherwise a long reply
                    # can leave response.done unread while the consumer sleeps.
                    async with asyncio.timeout(self.event_timeout_seconds):
                        event = json.loads(await self.websocket.recv())
                    event_type = event.get("type")
                    _raise_if_provider_error(event, "qwen-tts")
                    if event_type == "response.audio.delta":
                        events.put_nowait(base64.b64decode(str(event.get("delta") or "")))
                    if event_type == "response.done":
                        return
            except Exception as exc:
                events.put_nowait(exc)
            finally:
                events.put_nowait(None)

        receiver = asyncio.create_task(receive_response())
        try:
            while True:
                item = await events.get()
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            if not receiver.done():
                receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receiver

    async def finish(self) -> None:
        if self.closed:
            return
        await self.websocket.send(
            json.dumps({"event_id": f"event_{uuid.uuid4().hex}", "type": "session.finish"})
        )
        with contextlib.suppress(Exception):
            await self._wait_for("session.finished")
        await self.websocket.close()
        self.closed = True

    async def cancel(self) -> None:
        if not self.closed:
            await self.websocket.close()
            self.closed = True


@dataclass(frozen=True)
class RealtimeProviderBundle:
    settings: Settings
    llm: RealtimeLlmProvider
    mock: bool = False

    async def open_asr(self) -> RealtimeAsrSession:
        if self.mock:
            return MockAsrSession()
        return await QwenRealtimeAsrSession.open(self.settings)

    async def open_tts(self, voice: str, speech_rate: float = 1.0) -> RealtimeTtsSession:
        if self.mock:
            return MockTtsSession()
        return await QwenRealtimeTtsSession.open(
            self.settings, voice=voice, speech_rate=speech_rate
        )


def create_realtime_providers(settings: Settings) -> RealtimeProviderBundle:
    if settings.provider_mode == "mock":
        return RealtimeProviderBundle(settings=settings, llm=MockLlmProvider(), mock=True)
    return RealtimeProviderBundle(
        settings=settings,
        llm=DeepSeekStreamingLlmProvider(settings),
        mock=False,
    )
