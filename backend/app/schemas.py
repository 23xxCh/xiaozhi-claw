from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class DevLoginRequest(BaseModel):
    openid: str = Field(min_length=3, max_length=128)
    adult_confirmed: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class DeviceRegistrationRequest(BaseModel):
    serial_number: str = Field(pattern=r"^[A-Za-z0-9:-]{6,64}$")
    board_type: str = Field(default="hensun-desk-v1", pattern=r"^[a-z0-9.-]+$")


class DeviceRegistrationResponse(BaseModel):
    device_id: str
    serial_number: str
    device_secret: str
    lifecycle: str


class DeviceBootstrapRequest(BaseModel):
    firmware_version: str = Field(default="0.0.0", max_length=32)


class DeviceBootstrapResponse(BaseModel):
    claim_code: str
    expires_at: datetime
    lifecycle: str


class ClaimConfirmRequest(BaseModel):
    claim_code: str = Field(min_length=16, max_length=200)


class DeviceResponse(BaseModel):
    id: str
    serial_number: str
    board_type: str
    lifecycle: str
    memory_consent: bool
    firmware_version: str


class MemoryConsentRequest(BaseModel):
    enabled: bool


class MemoryUpsertRequest(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9._-]+$")
    value: str = Field(min_length=1, max_length=2000)


class MemoryResponse(BaseModel):
    id: str
    key: str
    value: str
    updated_at: datetime


class EntitlementResponse(BaseModel):
    plan: str
    monthly_turn_limit: int
    used_turns: int
    remaining_turns: int
    expires_at: datetime | None


class FirmwareReleaseRequest(BaseModel):
    board_type: str = Field(pattern=r"^[a-z0-9.-]+$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?$")
    artifact_url: HttpUrl
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str = Field(min_length=32)
    rollout_percent: int = Field(ge=0, le=100)
    mandatory: bool = False
    active: bool = False

    @field_validator("artifact_url")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("firmware artifact URL must use HTTPS")
        return value


class FirmwareReleaseResponse(BaseModel):
    board_type: str
    version: str
    artifact_url: str
    sha256: str
    signature: str
    rollout_percent: int
    mandatory: bool


class FaceEventName(StrEnum):
    READY = "ready"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    HAPPY = "happy"
    CURIOUS = "curious"
    CARING = "caring"
    REMINDER = "reminder"
    NETWORK_ERROR = "network_error"
    SLEEP = "sleep"
    LAUGHING = "laughing"
    FUNNY = "funny"
    LOVING = "loving"
    EMBARRASSED = "embarrassed"
    CONFIDENT = "confident"
    DELICIOUS = "delicious"
    SAD = "sad"
    CRYING = "crying"
    SLEEPY = "sleepy"
    SILLY = "silly"
    ANGRY = "angry"
    SURPRISED = "surprised"
    SHOCKED = "shocked"
    WINKING = "winking"
    RELAXED = "relaxed"
    CONFUSED = "confused"
    PROUD = "proud"
    EXCITED = "excited"
    WORRIED = "worried"
    APOLOGY = "apology"
    PAIRING = "pairing"
    NETWORK_OK = "network_ok"
    UPDATING = "updating"
    SAFE_BLOCK = "safe_block"


class DeviceFaceEventRequest(BaseModel):
    event: FaceEventName
    message_type: Literal["llm", "alert"] = "llm"
    status: str | None = Field(default=None, min_length=1, max_length=80)
    message: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_alert_copy(self) -> "DeviceFaceEventRequest":
        if self.message_type == "alert" and (not self.status or not self.message):
            raise ValueError("alert events require status and message")
        return self


class DeviceFaceEventResponse(BaseModel):
    serial_number: str
    event: FaceEventName
    message_type: Literal["llm", "alert"]
    delivered: bool
