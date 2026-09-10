"""Managed Aliyun application: one push-to-talk turn per upstream session.

The application owns persona, voice and cloud tools. Hensun never forwards
device commands from this provider. Completed dialogs can resume by ID without
overriding the application's persona or uploading transcript history.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import uuid4

from websockets.asyncio.client import connect

from .conversation_backend import ConversationEvent
from .providers import RealtimeProviderError, RealtimeProviderTimeout

PROVIDER = "aliyun-dialog"
telemetry_logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class AliyunDialogConfig:
    api_key: str = field(repr=False)
    url: str
    workspace_id: str
    app_id: str
    timeout_seconds: float = 15.0
    dialog_id: str = ""
    client_id: str = ""


class AliyunDialogBackend:
    def __init__(self, websocket, config: AliyunDialogConfig):
        self.websocket, self.config = websocket, config
        self.session_id = ""
        self.completed_dialog_id = ""
        self.task_id = str(uuid4())
        self.endpoint_event = asyncio.Event()
        self._listening = asyncio.Event()
        self._events: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._generation = 0
        self._turn_id = ""
        self._active = False
        self._closed = False
        self._finished = False
        self._input_started = False
        self._input_ended = False
        self._transcribed = False
        self._response_id = ""
        self._buffered_bytes = 0
        self._failure = None
        self._reader = None
        self._send_lock = asyncio.Lock()
        self._sent_pcm_bytes = 0

    @classmethod
    async def open(cls, config: AliyunDialogConfig):
        url = urlsplit(config.url)
        if (
            url.scheme != "wss"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or config.timeout_seconds <= 0
            or not all(
                x.strip() and "\n" not in x and "\r" not in x
                for x in (config.api_key, config.workspace_id, config.app_id)
            )
        ):
            raise RealtimeProviderError(PROVIDER, "invalid-config")
        try:
            ws = await connect(
                config.url,
                additional_headers={
                    "Authorization": "Bearer " + config.api_key,
                },
                proxy=None,
                open_timeout=config.timeout_seconds,
                close_timeout=2,
                max_size=1024 * 1024,
            )
        except Exception:
            raise RealtimeProviderError(PROVIDER, "connect-failed") from None
        backend = cls(ws, config)
        backend._reader = asyncio.create_task(backend._read())
        try:
            await backend._send(
                {
                    "task_group": "aigc",
                    "task": "multimodal-generation",
                    "function": "generation",
                    "model": "multimodal-dialog",
                    "input": {
                        "directive": "Start",
                        "workspace_id": config.workspace_id,
                        "app_id": config.app_id,
                        **({"dialog_id": config.dialog_id} if config.dialog_id else {}),
                    },
                    "parameters": {
                        "upstream": {
                            "type": "AudioOnly",
                            "mode": "push2talk",
                            "audio_format": "pcm",
                            "sample_rate": 16000,
                        },
                        "downstream": {"audio_format": "pcm", "sample_rate": 24000},
                        "client_info": {
                            "user_id": config.client_id or backend.task_id.replace("-", ""),
                            "device": {"uuid": config.client_id or backend.task_id},
                        },
                    },
                },
                action="run-task",
            )
            async with asyncio.timeout(config.timeout_seconds):
                await backend._listening.wait()
            if backend._failure:
                raise backend._failure
            return backend
        except BaseException:
            await backend.close()
            raise

    async def _send(self, payload, *, action="continue-task"):
        try:
            async with self._send_lock, asyncio.timeout(self.config.timeout_seconds):
                await self.websocket.send(
                    json.dumps(
                        {
                            "header": {
                                "action": action,
                                "task_id": self.task_id,
                                "streaming": "duplex",
                            },
                            "payload": payload,
                        }
                    )
                )
        except TimeoutError:
            raise RealtimeProviderTimeout(PROVIDER, "send") from None
        except Exception:
            raise RealtimeProviderError(PROVIDER, "send-failed") from None

    async def _directive(self, name, *, action="continue-task"):
        await self._send(
            {"input": {"directive": name, "dialog_id": self.session_id}}, action=action
        )
        telemetry_logger.info(
            "aliyun input turn=%s directive=%s pcm_bytes=%d",
            self._turn_id,
            name,
            self._sent_pcm_bytes,
        )

    def begin_turn(self, turn_id: str) -> int:
        if self._turn_id or self._closed or self._failure or not turn_id:
            raise RealtimeProviderError(PROVIDER, "session-not-reusable")
        self._turn_id = turn_id
        self._generation += 1
        self._active = True
        return self._generation

    def _current(self, generation):
        return self._active and not self._closed and generation == self._generation

    async def send_audio(self, pcm: bytes, *, generation: int):
        if not self._current(generation) or self._input_ended or self.endpoint_detected():
            return
        if not pcm or len(pcm) % 2 or len(pcm) > 64000:
            raise RealtimeProviderError(PROVIDER, "invalid-input-pcm")
        if not self._input_started:
            await self._directive("SendSpeech")
            self._input_started = True
        try:
            async with self._send_lock, asyncio.timeout(self.config.timeout_seconds):
                if self._current(generation):
                    await self.websocket.send(pcm)
                    self._sent_pcm_bytes += len(pcm)
                    if self._sent_pcm_bytes == len(pcm):
                        telemetry_logger.info(
                            "aliyun input turn=%s first_pcm_sent bytes=%d", self._turn_id, len(pcm)
                        )
        except Exception:
            raise RealtimeProviderError(PROVIDER, "audio-send-failed") from None

    async def end_input(self, *, generation: int):
        if not self._current(generation) or self._input_ended:
            return
        self._input_ended = True
        if not self._input_started:
            raise RealtimeProviderError(PROVIDER, "empty-input")
        if not self.endpoint_detected():
            await self._directive("StopSpeech")

    def endpoint_detected(self):
        return self.endpoint_event.is_set()

    async def mute(self):
        await self.end_input(generation=self._generation)

    async def unmute(self):
        # push2talk starts only with the first actual audio frame.
        return

    def _clear(self):
        while not self._events.empty():
            self._events.get_nowait()
        self._buffered_bytes = 0

    def _emit(self, kind, **data):
        event = ConversationEvent(
            type=kind,
            generation=self._generation,
            turn_id=self._turn_id,
            response_id=self._response_id,
            **data,
        )
        if self._events.full() or self._buffered_bytes + len(event.audio) > 2 * 1024 * 1024:
            raise RealtimeProviderError(PROVIDER, "event-buffer-full")
        self._buffered_bytes += len(event.audio)
        self._events.put_nowait(event)

    async def _read(self):
        try:
            while not self._closed:
                async with asyncio.timeout(self.config.timeout_seconds):
                    raw = await self.websocket.recv()
                if isinstance(raw, bytes):
                    if self._active and not self._finished:
                        if len(raw) % 2:
                            raise RealtimeProviderError(PROVIDER, "invalid-output-pcm")
                        self._emit("audio", audio=raw)
                    continue
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("invalid envelope")
                header = message.get("header", {})
                if header.get("task_id") != self.task_id:
                    continue
                if header.get("event") == "task-failed":
                    raise RealtimeProviderError(PROVIDER, "provider-rejected")
                output = message.get("payload", {}).get("output", {})
                kind = output.get("event")
                if kind in {
                    "Started",
                    "DialogStateChanged",
                    "SpeechContent",
                    "SpeechStarted",
                    "SpeechEnded",
                    "RespondingEnded",
                }:
                    telemetry_logger.info(
                        "aliyun event turn=%s kind=%s finished=%s "
                        "text_chars=%d pcm_sent=%d state=%s",
                        self._turn_id,
                        kind,
                        output.get("finished") is True,
                        len(output.get("text", "")) if isinstance(output.get("text"), str) else 0,
                        self._sent_pcm_bytes,
                        output.get("state") if output.get("state") in {
                            "Listening", "Thinking", "Responding", "Idle"
                        } else "other",
                    )
                if kind == "Started":
                    if not isinstance(output.get("dialog_id"), str) or not output["dialog_id"]:
                        raise ValueError("missing session")
                    self.session_id = output["dialog_id"]
                if output.get("dialog_id") != self.session_id or not self.session_id:
                    continue
                if kind == "DialogStateChanged" and output.get("state") == "Listening":
                    self._listening.set()
                if not self._active or self._finished:
                    continue
                round_id = output.get("round_id")
                if round_id:
                    if not isinstance(round_id, str):
                        raise ValueError("invalid round")
                    if self._response_id and self._response_id != round_id:
                        raise RealtimeProviderError(PROVIDER, "unexpected-round")
                    self._response_id = round_id
                if kind == "SpeechContent" and output.get("finished") is True:
                    if not self._transcribed:
                        if not isinstance(output.get("text"), str):
                            raise ValueError("invalid transcript")
                        self._transcribed = True
                        self._emit("transcript_final", text=output["text"])
                elif kind == "SpeechEnded":
                    if not self.endpoint_detected():
                        self.endpoint_event.set()
                        self._emit("endpoint")
                elif kind == "RespondingContent":
                    if not isinstance(output.get("spoken"), str):
                        raise ValueError("invalid spoken text")
                    # Both fields are cumulative, not deltas. Check each revision.
                    self._emit("text_final", text=output["spoken"])
                elif kind == "RespondingEnded":
                    self._finished = True
                    self._emit("audio_done")
                    self._emit("done")
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._closed:
                return
            self._failure = (
                exc
                if isinstance(exc, RealtimeProviderError)
                else RealtimeProviderError(PROVIDER, "receive-failed")
            )
            self._active = False
            self._clear()
            self._events.put_nowait(self._failure)
            self.endpoint_event.set()
            self._listening.set()

    async def events(self) -> AsyncIterator[ConversationEvent]:
        while True:
            item = await self._events.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            self._buffered_bytes -= len(item.audio)
            if self._current(item.generation):
                yield item
                if item.type == "done":
                    return

    async def playback_completed(self):
        # Only the gateway's device drain acknowledgment may trigger this.
        if self._finished and not self._closed:
            await self._directive("LocalRespondingEnded")
            self.completed_dialog_id = self.session_id

    def invalidate(self, *, generation: int):
        if generation != self._generation:
            return
        self._active = False
        self.endpoint_event.set()
        self._clear()
        self._events.put_nowait(None)

    async def cancel(self, *, generation: int):
        if generation == self._generation:
            self.invalidate(generation=generation)
            await self.close()

    async def close(self):
        if self._closed:
            return
        self.invalidate(generation=self._generation)
        self._closed = True
        with contextlib.suppress(Exception):
            async with asyncio.timeout(2):
                if self.session_id:
                    await self._directive("Stop", action="finish-task")
        with contextlib.suppress(Exception):
            async with asyncio.timeout(2):
                await self.websocket.close()
        if self._reader:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
