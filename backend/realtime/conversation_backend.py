"""Small event boundary shared by cascaded and speech-to-speech conversations."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True)
class ConversationMessage:
    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class ConversationEvent:
    type: Literal[
        "transcript_delta",
        "transcript_final",
        "endpoint",
        "text_delta",
        "text_final",
        "audio",
        "audio_done",
        "usage",
        "done",
    ]
    generation: int
    turn_id: str
    text: str = ""
    audio: bytes = field(default=b"", repr=False)
    question_id: str = ""
    response_id: str = ""
    usage: dict[str, object] | None = None


class ConversationConfig(Protocol):
    @property
    def timeout_seconds(self) -> float: ...


class ConversationBackend(Protocol):
    # One controlled provider session per turn initially. Open/selection belongs
    # to the concrete factory; existing ASR/LLM/TTS Protocols remain unchanged.
    config: ConversationConfig
    session_id: str
    endpoint_event: asyncio.Event

    def begin_turn(self, turn_id: str) -> int: ...

    async def send_audio(self, pcm: bytes, *, generation: int) -> None: ...

    async def end_input(self, *, generation: int) -> None: ...

    def endpoint_detected(self) -> bool: ...

    async def mute(self) -> None: ...

    async def unmute(self) -> None: ...

    def invalidate(self, *, generation: int) -> None: ...

    async def cancel(self, *, generation: int) -> None: ...

    def events(self) -> AsyncIterator[ConversationEvent]: ...

    async def playback_completed(self) -> None: ...

    async def close(self) -> None: ...
