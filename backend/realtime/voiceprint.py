"""Voiceprint boundary for the optional identity feature.

This module intentionally contains no biometric implementation.  It gives
the gateway a replaceable, auditable interface without accepting raw audio
or enabling family mode by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SpeakerVerificationResult:
    identity: str | None
    confidence: float
    status: str


class SpeakerVerificationProvider(Protocol):
    async def verify(self, audio: bytes) -> SpeakerVerificationResult: ...


class DisabledSpeakerVerificationProvider:
    """Safe default: normal conversation never depends on voiceprint."""

    async def verify(self, audio: bytes) -> SpeakerVerificationResult:
        del audio
        return SpeakerVerificationResult(None, 0.0, "disabled")
