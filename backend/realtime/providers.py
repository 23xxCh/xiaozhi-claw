import asyncio
import base64
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import ClientConnection, connect

from backend.ai.context import LlmRequest
from backend.app.audio_formats import IncrementalOggOpusMuxer
from backend.app.config import Settings
from backend.app.voice_routes import validate_tts_admission

logger = logging.getLogger(__name__)
ASR_BACKGROUND_CLOSE_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    emotion: str = "neutral"
    transcription_completed_at: float | None = None
    session_finished_at: float | None = None


class RealtimeProviderError(RuntimeError):
    def __init__(self, provider: str, code: str) -> None:
        self.provider = provider
        self.code = code[:80] or "unknown"
        super().__init__(f"{provider} realtime provider error: {self.code}")


class RealtimeProviderTimeout(RuntimeError):
    def __init__(self, provider: str, stage: str) -> None:
        self.provider = provider
        self.stage = stage
        super().__init__(f"{provider} realtime provider timeout: {stage}")


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
        request: LlmRequest,
        *,
        tool_executor: Callable[[str, dict[str, object]], Awaitable[str]] | None = None,
    ) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


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
        request: LlmRequest,
        *,
        tool_executor: Callable[[str, dict[str, object]], Awaitable[str]] | None = None,
    ) -> AsyncIterator[str]:
        del tool_executor
        transcript = str(request.context.messages[-1].get("content", ""))
        yield f"收到：{transcript}"

    async def aclose(self) -> None:
        return None


class MockTtsSession:
    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        yield text.encode()

    async def finish(self) -> None:
        return None

    async def cancel(self) -> None:
        return None


class QwenRealtimeAsrSession:
    def __init__(self, websocket: ClientConnection, *, buffer_events: int = 256,
                 buffer_bytes: int = 2 * 1024 * 1024) -> None:
        self.buffer_bytes = buffer_bytes
        self._queued_bytes = 0
        self.websocket = websocket
        self.closed = False
        self.ogg_muxer = IncrementalOggOpusMuxer(
            input_sample_rate=16000, frame_duration_ms=60
        )
        self._endpoint = asyncio.Event()
        self._events: asyncio.Queue[dict[str, object] | BaseException] = asyncio.Queue(
            maxsize=buffer_events
        )
        self._reader_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None

    @classmethod
    async def open(cls, settings: Settings) -> "QwenRealtimeAsrSession":
        url = (
            f"{settings.qwen_realtime_asr_url.rstrip('/')}?"
            f"{urlencode({'model': settings.qwen_realtime_asr_model})}"
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
        session = cls(websocket, buffer_events=settings.provider_event_buffer_size,
                      buffer_bytes=settings.provider_audio_buffer_bytes)
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
                raw = await self.websocket.recv()
                size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
                if self._events.full() or self._queued_bytes + size > self.buffer_bytes:
                    raise RealtimeProviderError("qwen-asr", "buffer-overflow")
                event = json.loads(raw)
                event["_buffer_bytes"] = size
                event["_gateway_received_at"] = time.perf_counter()
                _raise_if_provider_error(event, "qwen-asr")
                if event.get("type") == "input_audio_buffer.speech_stopped":
                    self._endpoint.set()
                self._events.put_nowait(event)
                self._queued_bytes += size
                if event.get("type") == "session.finished":
                    return
        except BaseException as exc:
            # Wake the gateway immediately so finish() can surface the stable
            # provider error and use its bounded batch fallback.
            self._endpoint.set()
            while not self._events.empty():
                self._events.get_nowait()
            self._queued_bytes = 0
            self._events.put_nowait(exc)

    def endpoint_detected(self) -> bool:
        return self._endpoint.is_set()

    async def _wait_for(self, expected: str) -> dict[str, object]:
        try:
            async with asyncio.timeout(15):
                while True:
                    event = json.loads(await self.websocket.recv())
                    _raise_if_provider_error(event, "qwen-asr")
                    if event.get("type") == expected:
                        return event
        except TimeoutError as exc:
            raise RealtimeProviderTimeout("qwen-asr", expected) from exc

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
        transcription_completed_at: float | None = None
        session_finished_at: float | None = None
        try:
            async with asyncio.timeout(30):
                while True:
                    event = await self._events.get()
                    if isinstance(event, TimeoutError):
                        raise RealtimeProviderTimeout("qwen-asr", "session-finish") from event
                    if isinstance(event, BaseException):
                        raise event
                    self._queued_bytes -= int(event.pop("_buffer_bytes", 0))
                    event_type = event.get("type")
                    if event_type in {
                        "conversation.item.input_audio_transcription.text",
                        "conversation.item.input_audio_transcription.completed",
                    }:
                        text = str(event.get("transcript") or event.get("text") or text)
                        emotion = str(event.get("emotion") or emotion)
                    if event_type == "conversation.item.input_audio_transcription.completed":
                        transcription_completed_at = float(
                            event.get("_gateway_received_at") or time.perf_counter()
                        )
                    if event_type == "session.finished":
                        session_finished_at = float(
                            event.get("_gateway_received_at") or time.perf_counter()
                        )
                        break
        except TimeoutError as exc:
            raise RealtimeProviderTimeout("qwen-asr", "session-finish") from exc
        result = TranscriptionResult(
            text.strip(),
            emotion,
            transcription_completed_at=transcription_completed_at,
            session_finished_at=session_finished_at,
        )
        self._close_task = asyncio.create_task(self._close_after_finish())
        return result

    async def _close_after_finish(self) -> None:
        try:
            async with asyncio.timeout(ASR_BACKGROUND_CLOSE_TIMEOUT_SECONDS):
                await self.websocket.close()
                if self._reader_task is not None:
                    await self._reader_task
        except TimeoutError:
            logger.warning("qwen ASR close timed out after session finished")
        except Exception as exc:
            logger.warning(
                "qwen ASR close failed after session finished: %s",
                type(exc).__name__,
            )
        finally:
            if self._reader_task is not None and not self._reader_task.done():
                self._reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reader_task
            self.closed = True

    async def cancel(self) -> None:
        if self._close_task is not None:
            await self._close_task
            return
        if not self.closed:
            await self.websocket.close()
            if self._reader_task is not None:
                self._reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reader_task
            self.closed = True


class DeepSeekStreamingLlmProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        deepseek_options: bool = True,
        owns_client: bool = True,
    ) -> None:
        self.settings = settings
        self._client = client or httpx.AsyncClient(
            timeout=self.settings.provider_timeout_seconds
        )
        self._closed = False
        self._deepseek_options = deepseek_options
        self._owns_client = owns_client

    def for_provider(
        self, provider: str, *, settings: Settings | None = None
    ) -> "DeepSeekStreamingLlmProvider":
        if provider not in {"deepseek", "openai-compatible"}:
            raise RealtimeProviderError("binding", "unsupported-llm-provider")
        return DeepSeekStreamingLlmProvider(
            settings or self.settings,
            client=self._client,
            deepseek_options=provider == "deepseek",
            owns_client=False,
        )

    async def aclose(self) -> None:
        if self._closed or not self._owns_client:
            return
        self._closed = True
        await self._client.aclose()

    async def reply_stream(
        self,
        request: LlmRequest,
        *,
        tool_executor: Callable[[str, dict[str, object]], Awaitable[str]] | None = None,
    ) -> AsyncIterator[str]:
        messages = [dict(message) for message in request.context.messages]
        base_url = self.settings.llm_url.rstrip("/")
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        tool_result_added = False
        for _ in range(3):
            payload: dict[str, object] = {
                "model": request.model,
                "messages": messages,
                "temperature": request.temperature,
                "max_tokens": request.max_output_tokens,
                "stream": True,
            }
            if self._deepseek_options:
                # This option is private to DeepSeek, not part of the shared
                # OpenAI-compatible chat completions protocol.
                payload["thinking"] = {"type": "disabled"}
            if request.tools:
                payload["tools"] = request.tools
                if tool_result_added:
                    payload["tool_choice"] = "none"
            tool_calls: dict[int, dict[str, str]] = {}
            assistant_parts: list[str] = []
            reasoning_parts: list[str] = []
            try:
                async with self._client.stream(
                    "POST",
                    url,
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json=payload,
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
            except httpx.TimeoutException as exc:
                raise RealtimeProviderTimeout("deepseek", "response-stream") from exc
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
    EMOTION_INSTRUCTIONS = {
        "neutral": "自然平静地说话。", "happy": "用开心、轻快的语气说话。",
        "laughing": "用愉快、带笑意的语气说话。", "caring": "用温柔关心、安慰的语气说话。",
        "affectionate": "用温暖亲切的语气说话。", "curious": "用好奇、感兴趣的语气说话。",
        "surprised": "用惊讶的语气说话。", "confused": "用疑惑的语气说话。",
        "concerned": "用关切担忧的语气说话。", "apologetic": "用真诚歉意的语气说话。",
        "shy": "用害羞、轻柔的语气说话。", "sad": "用低落难过的语气说话。",
        "angry": "用生气但克制的语气说话，不尖叫。",
    }
    def __init__(
        self,
        websocket: ClientConnection,
        *,
        event_timeout_seconds: float = 30.0,
        buffer_events: int = 256,
        buffer_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self.buffer_events = buffer_events
        self.buffer_bytes = buffer_bytes
        self.websocket = websocket
        self.event_timeout_seconds = event_timeout_seconds
        self.closed = False
        self.instruction_control = False
        self._configuration: dict[str, object] = {}
        self._emotion: str | None = None
        self._connect_url = ""
        self._connect_options: dict[str, object] = {}

    @classmethod
    async def open(
        cls, settings: Settings, *, voice: str, speech_rate: float
    ) -> "QwenRealtimeTtsSession":
        validate_tts_admission(settings, "dashscope", settings.qwen_realtime_tts_model)
        url = (
            f"{settings.qwen_realtime_tts_url.rstrip('/')}?"
            f"{urlencode({'model': settings.qwen_realtime_tts_model})}"
        )
        connect_options = {
            "proxy": None,
            "additional_headers": {
                "Authorization": f"Bearer {settings.tts_api_key}",
                "OpenAI-Beta": "realtime=v1",
            },
            "open_timeout": settings.provider_timeout_seconds,
            "max_size": 2 * 1024 * 1024,
        }
        websocket = await connect(url, **connect_options)
        session = cls(
            websocket,
            event_timeout_seconds=settings.provider_timeout_seconds,
            buffer_events=settings.provider_event_buffer_size,
            buffer_bytes=settings.provider_audio_buffer_bytes,
        )
        session.instruction_control = settings.qwen_realtime_tts_model.startswith(
            "qwen3-tts-instruct-flash-realtime"
        )
        session._configuration = {
            "voice": voice, "mode": "commit", "language_type": "Chinese",
            "response_format": "pcm", "sample_rate": 24000, "speech_rate": speech_rate,
        }
        session._connect_url = url
        session._connect_options = connect_options
        # Instruct accepts configuration only once, after the reply emotion is known.
        if not session.instruction_control:
            try:
                await websocket.send(json.dumps({
                    "event_id": f"event_{uuid.uuid4().hex}",
                    "type": "session.update", "session": session._configuration,
                }))
                await session._wait_for("session.updated")
            except BaseException:
                await websocket.close()
                raise
        return session

    async def set_emotion(self, emotion: str) -> None:
        if self.closed:
            raise RealtimeProviderError("qwen-tts", "closed")
        if not self.instruction_control:
            return
        emotion = emotion if emotion in self.EMOTION_INSTRUCTIONS else "neutral"
        if emotion == self._emotion:
            return
        if self._emotion is not None:
            await self.websocket.close()
            websocket = await connect(self._connect_url, **self._connect_options)
            if self.closed:
                await websocket.close()
                raise RealtimeProviderError("qwen-tts", "closed")
            self.websocket = websocket
        await self.websocket.send(json.dumps({
            "event_id": f"event_{uuid.uuid4().hex}", "type": "session.update",
            "session": {**self._configuration,
                        "instructions": self.EMOTION_INSTRUCTIONS[emotion],
                        "optimize_instructions": False},
        }))
        await self._wait_for("session.updated")
        self._emotion = emotion

    async def _wait_for(self, expected: str) -> dict[str, object]:
        try:
            async with asyncio.timeout(15):
                while True:
                    event = json.loads(await self.websocket.recv())
                    _raise_if_provider_error(event, "qwen-tts")
                    if event.get("type") == expected:
                        return event
        except TimeoutError as exc:
            raise RealtimeProviderTimeout("qwen-tts", expected) from exc

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        if self.closed:
            raise RealtimeProviderError("qwen-tts", "closed")
        if self.instruction_control and self._emotion is None:
            await self.set_emotion("neutral")
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
        events: asyncio.Queue[bytes | Exception | None] = asyncio.Queue(
            maxsize=self.buffer_events + 1
        )
        queued_bytes = 0

        async def receive_response() -> None:
            nonlocal queued_bytes
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
                        pcm = base64.b64decode(str(event.get("delta") or ""), validate=True)
                        if (events.qsize() >= self.buffer_events
                                or queued_bytes + len(pcm) > self.buffer_bytes):
                            raise RealtimeProviderError("qwen-tts", "buffer-overflow")
                        queued_bytes += len(pcm)
                        events.put_nowait(pcm)
                    if event_type == "response.done":
                        return
            except Exception as exc:
                while not events.empty():
                    events.get_nowait()
                queued_bytes = 0
                error = (RealtimeProviderTimeout("qwen-tts", "response-event")
                         if isinstance(exc, TimeoutError) else exc)
                events.put_nowait(error)
            finally:
                events.put_nowait(None)

        receiver = asyncio.create_task(receive_response())
        received_audio = False
        try:
            while True:
                item = await events.get()
                if self.closed:
                    raise RealtimeProviderError("qwen-tts", "closed")
                if item is None:
                    if not received_audio:
                        raise RealtimeProviderError("qwen-tts", "empty-audio")
                    return
                if isinstance(item, Exception):
                    raise item
                queued_bytes -= len(item)
                if not item:
                    continue
                received_audio = True
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
            self.closed = True
            await self.websocket.close()


class VolcTtsSession:
    """V3 SSE audio output, one request per existing sentence boundary."""

    def __init__(self, settings: Settings, voice: str, speech_rate: float) -> None:
        validate_tts_admission(settings, "volc-tts", "seed-tts-2.0")
        if not 0.5 <= speech_rate <= 2.0:
            raise RealtimeProviderError("volc-tts", "invalid-speech-rate")
        self.settings = settings
        self.voice = voice
        self.speech_rate = round((speech_rate - 1) * 100)
        self.client = httpx.AsyncClient(timeout=settings.provider_timeout_seconds, trust_env=False)
        self.closed = False

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        if self.closed:
            raise RealtimeProviderError("volc-tts", "closed")
        received = False
        try:
            async with self.client.stream(
                "POST", self.settings.volc_tts_url,
                headers={"X-Api-Key": self.settings.volc_tts_api_key,
                         "X-Api-Resource-Id": "seed-tts-2.0",
                         "X-Api-Request-Id": str(uuid.uuid4())},
                json={"user": {"uid": "hensun"}, "req_params": {
                    "text": text, "speaker": self.voice,
                    "audio_params": {"format": "pcm", "sample_rate": 24000,
                                     "speech_rate": self.speech_rate},
                }},
            ) as response:
                if response.status_code != 200:
                    raise RealtimeProviderError("volc-tts", f"http-{response.status_code}")
                async for line in response.aiter_lines():
                    if self.closed:
                        return
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:])
                    code = event.get("code")
                    if code not in (0, 20000000):
                        raise RealtimeProviderError("volc-tts", str(code))
                    if event.get("data"):
                        pcm = base64.b64decode(event["data"], validate=True)
                        if len(pcm) % 2:
                            raise RealtimeProviderError("volc-tts", "invalid-pcm")
                        if pcm:
                            received = True
                            yield pcm
                    if code == 20000000:
                        if not received:
                            raise RealtimeProviderError("volc-tts", "empty-audio")
                        return
                raise RealtimeProviderError("volc-tts", "incomplete-stream")
        except httpx.TimeoutException as exc:
            raise RealtimeProviderTimeout("volc-tts", "audio-stream") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise RealtimeProviderError("volc-tts", "invalid-stream") from exc

    async def finish(self) -> None:
        await self.cancel()

    async def cancel(self) -> None:
        if not self.closed:
            self.closed = True
            await self.client.aclose()


@dataclass(frozen=True)
class RealtimeProviderBundle:
    settings: Settings
    llm: RealtimeLlmProvider
    mock: bool = False
    _owns_llm: bool = field(default=True, repr=False)
    tts_provider: str = "dashscope"

    def for_models(
        self,
        *,
        asr_provider: str,
        asr_model: str,
        llm_provider: str,
        llm_model: str,
        tts_provider: str,
        tts_model: str,
    ) -> "RealtimeProviderBundle":
        """Freeze supported per-turn model choices without opening another pool.

        Provider IDs describe supported wire protocols. Endpoints and credentials
        remain application configuration; unsupported choices never fall through
        to an unrelated implementation. Only the application bundle owns its LLM.
        """
        if asr_provider != "dashscope":
            raise RealtimeProviderError("binding", "unsupported-asr-provider")
        if tts_provider not in {"dashscope", "volc-tts"}:
            raise RealtimeProviderError("binding", "unsupported-tts-provider")
        if tts_provider == "volc-tts" and tts_model != "seed-tts-2.0":
            raise RealtimeProviderError("binding", "unsupported-tts-model")
        if llm_provider not in {"deepseek", "openai-compatible"}:
            raise RealtimeProviderError("binding", "unsupported-llm-provider")
        if any(not isinstance(model, str) or not model.strip() for model in (
            asr_model, llm_model, tts_model,
        )):
            raise RealtimeProviderError("binding", "missing-model")
        settings = self.settings.model_copy(deep=True, update={
            "asr_model": asr_model, "qwen_realtime_asr_model": asr_model,
            "llm_model": llm_model,
            "tts_model": tts_model, "qwen_realtime_tts_model": tts_model,
        })
        llm = self.llm
        if not self.mock:
            if not isinstance(llm, DeepSeekStreamingLlmProvider):
                raise RealtimeProviderError("binding", "unsupported-llm-implementation")
            llm = llm.for_provider(llm_provider, settings=settings)
        return RealtimeProviderBundle(
            settings, llm, self.mock, _owns_llm=False, tts_provider=tts_provider
        )

    async def open_asr(self) -> RealtimeAsrSession:
        if self.mock:
            return MockAsrSession()
        return await QwenRealtimeAsrSession.open(self.settings)

    async def open_tts(self, voice: str, speech_rate: float = 1.0) -> RealtimeTtsSession:
        if self.mock:
            return MockTtsSession()
        if self.tts_provider == "volc-tts":
            return VolcTtsSession(self.settings, voice, speech_rate)
        return await QwenRealtimeTtsSession.open(
            self.settings, voice=voice, speech_rate=speech_rate
        )

    async def aclose(self) -> None:
        if self._owns_llm:
            await self.llm.aclose()


def create_realtime_providers(settings: Settings) -> RealtimeProviderBundle:
    if settings.provider_mode == "mock":
        return RealtimeProviderBundle(settings=settings, llm=MockLlmProvider(), mock=True)
    return RealtimeProviderBundle(
        settings=settings,
        llm=DeepSeekStreamingLlmProvider(settings),
        mock=False,
    )
