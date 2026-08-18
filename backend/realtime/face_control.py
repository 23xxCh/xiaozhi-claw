from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FACE_CONTROL_PREFIX = "[[face:"
SUPPORTED_FACE_EMOTIONS = frozenset(
    {
        "neutral",
        "happy",
        "laughing",
        "caring",
        "affectionate",
        "curious",
        "surprised",
        "confused",
        "concerned",
        "apologetic",
    }
)
_SENTENCE_BOUNDARIES = frozenset("。！？!?；;\n")


@dataclass(frozen=True)
class FaceControlEvent:
    kind: Literal["text", "emotion"]
    value: str


class FaceControlParser:
    """Removes streamed face controls and emits at most two safe emotion events."""

    def __init__(self) -> None:
        self._buffer = ""
        self._visible_text_seen = False
        self._at_sentence_boundary = True
        self._accepted_emotions = 0

    def feed(self, chunk: str) -> list[FaceControlEvent]:
        if not chunk:
            return []
        self._buffer += chunk
        return self._drain(final=False)

    def flush(self) -> list[FaceControlEvent]:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[FaceControlEvent]:
        events: list[FaceControlEvent] = []
        while self._buffer:
            marker_start = self._buffer.find(FACE_CONTROL_PREFIX)
            if marker_start < 0:
                emit_count = len(self._buffer) if final else self._safe_text_prefix_length()
                if emit_count == 0:
                    break
                self._emit_text(self._buffer[:emit_count], events)
                self._buffer = self._buffer[emit_count:]
                continue

            if marker_start > 0:
                self._emit_text(self._buffer[:marker_start], events)
                self._buffer = self._buffer[marker_start:]
                continue

            marker_end = self._buffer.find("]]", len(FACE_CONTROL_PREFIX))
            if marker_end >= 0:
                emotion = self._buffer[len(FACE_CONTROL_PREFIX) : marker_end].strip().lower()
                self._buffer = self._buffer[marker_end + 2 :]
                if self._accepts(emotion):
                    self._accepted_emotions += 1
                    events.append(FaceControlEvent("emotion", emotion))
                continue

            single_bracket = self._buffer.find("]", len(FACE_CONTROL_PREFIX))
            if single_bracket >= 0:
                if single_bracket + 1 == len(self._buffer) and not final:
                    break
                if (
                    single_bracket + 1 < len(self._buffer)
                    and self._buffer[single_bracket + 1] == "]"
                ):
                    continue
                self._buffer = self._buffer[single_bracket + 1 :]
                continue

            if final:
                self._buffer = ""
            break
        return events

    def _safe_text_prefix_length(self) -> int:
        max_suffix = min(len(self._buffer), len(FACE_CONTROL_PREFIX) - 1)
        for size in range(max_suffix, 0, -1):
            if FACE_CONTROL_PREFIX.startswith(self._buffer[-size:]):
                return len(self._buffer) - size
        return len(self._buffer)

    def _accepts(self, emotion: str) -> bool:
        if emotion not in SUPPORTED_FACE_EMOTIONS or self._accepted_emotions >= 2:
            return False
        if self._accepted_emotions == 0:
            return not self._visible_text_seen
        return self._at_sentence_boundary

    def _emit_text(self, value: str, events: list[FaceControlEvent]) -> None:
        if not value:
            return
        events.append(FaceControlEvent("text", value))
        stripped = value.rstrip()
        if not stripped:
            return
        self._visible_text_seen = True
        self._at_sentence_boundary = stripped[-1] in _SENTENCE_BOUNDARIES
