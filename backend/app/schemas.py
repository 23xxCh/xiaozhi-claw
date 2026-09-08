import re
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator, model_validator


class DevLoginRequest(BaseModel):
    openid: str = Field(min_length=3, max_length=128)
    adult_confirmed: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class EmailCodeRequest(BaseModel):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email_input(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class EmailCodeRequestResponse(BaseModel):
    expires_in: int
    resend_after: int
    debug_code: str | None = None


class EmailCodeVerifyRequest(EmailCodeRequest):
    code: str = Field(pattern=r"^\d{6}$")


class EmailLoginResponse(TokenResponse):
    agreements_complete: bool


class DeviceRegistrationRequest(BaseModel):
    serial_number: str = Field(pattern=r"^[A-Za-z0-9:-]{6,64}$")
    board_type: str = Field(default="hensun-desk-v1", pattern=r"^[a-z0-9.-]+$")


class DeviceRegistrationResponse(BaseModel):
    device_id: str
    serial_number: str
    device_secret: str
    lifecycle: str


class DeviceCredentialRotationRequest(BaseModel):
    confirm: Literal[True]


class DeviceBatchRegistrationRequest(BaseModel):
    devices: list[DeviceRegistrationRequest] = Field(min_length=1, max_length=500)
    confirm: bool = False

    @model_validator(mode="after")
    def validate_batch(self) -> "DeviceBatchRegistrationRequest":
        serials = [item.serial_number for item in self.devices]
        if len(serials) != len(set(serials)):
            raise ValueError("batch contains duplicate serial numbers")
        if not self.confirm:
            raise ValueError("factory batch registration requires explicit confirmation")
        return self


class DeviceBootstrapRequest(BaseModel):
    firmware_version: str = Field(default="0.0.0", max_length=32)


class DeviceBootstrapResponse(BaseModel):
    claim_code: str | None = None
    expires_at: datetime | None = None
    lifecycle: str
    websocket: dict[str, object] | None = None


class ClaimConfirmRequest(BaseModel):
    claim_code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class DeviceResponse(BaseModel):
    id: str
    serial_number: str
    board_type: str
    lifecycle: str
    memory_consent: bool
    firmware_version: str


class DeviceDetailResponse(DeviceResponse):
    name: str
    hardware_version: str
    ota_auto_update: bool
    active_agent_id: str | None
    active_profile_id: str | None
    online: bool
    last_seen_at: datetime | None


class DeviceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    ota_auto_update: bool | None = None
    active_agent_id: str | None = None


class DeviceConfigurationUpdateRequest(BaseModel):
    speaker_volume: int = Field(ge=10, le=100)
    screen_brightness: int = Field(ge=10, le=100)


class DeviceConfigurationResponse(BaseModel):
    device_id: str
    desired_version: int
    applied_version: int
    speaker_volume: int
    screen_brightness: int
    applied_speaker_volume: int | None
    applied_screen_brightness: int | None
    sync_status: Literal["unknown", "pending", "synced", "failed"]
    last_error_code: str | None
    command_id: str | None = None
    updated_at: datetime
    applied_at: datetime | None


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


class AdultConfirmationRequest(BaseModel):
    confirmed: bool
    accepted_terms: bool
    accepted_privacy: bool
    acknowledged_ai: bool


class UserResponse(BaseModel):
    id: str
    email: str | None
    display_name: str
    adult_confirmed: bool
    agreements_complete: bool


class WechatLoginStartResponse(BaseModel):
    authorization_url: str


class AgentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    avatar_url: HttpUrl | None = None
    system_prompt: str = Field(
        default="你是 Hensun Desk，一位自然、可靠的桌面 AI 助手。",
        min_length=1,
        max_length=8000,
    )
    model_preset_id: str | None = Field(default=None, min_length=1, max_length=64)
    voice_preset_id: str | None = Field(default=None, min_length=1, max_length=64)
    llm_temperature: float = Field(default=0.6, ge=0, le=2)
    tts_speech_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    usage_profile_id: str | None = None


class AgentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    avatar_url: HttpUrl | None = None
    system_prompt: str | None = Field(default=None, min_length=1, max_length=8000)
    model_preset_id: str | None = Field(default=None, max_length=64)
    voice_preset_id: str | None = Field(default=None, max_length=64)
    memory_consent: bool | None = None
    tools: dict[str, bool] | None = None
    llm_temperature: float | None = Field(default=None, ge=0, le=2)
    tts_speech_rate: float | None = Field(default=None, ge=0.5, le=2.0)


class AgentResponse(BaseModel):
    id: str
    usage_profile_id: str
    name: str
    avatar_url: str | None
    system_prompt: str
    model_preset_id: str
    voice_preset_id: str
    memory_consent: bool
    tools: dict[str, bool]
    llm_temperature: float
    tts_speech_rate: float
    config_version: int
    device_count: int = 0
    created_at: datetime
    updated_at: datetime


class RouteCapabilities(BaseModel):
    system_prompt: bool = True
    history: bool = True
    llm_temperature: bool
    tts_speech_rate: bool
    tools: bool
    supported_tool_ids: list[str] = Field(default_factory=list)


class ModelPresetResponse(BaseModel):
    id: str
    display_name: str
    description: str
    is_default: bool
    route_kind: Literal["cascade", "realtime_s2s", "managed_app"]
    capabilities: RouteCapabilities
    compatible_voice_ids: list[str]
    default_voice_preset_id: str | None


class AdminModelPresetResponse(ModelPresetResponse):
    realtime_provider: str | None
    realtime_model: str | None
    asr_provider: str | None
    asr_model: str | None
    llm_provider: str | None
    llm_model: str | None
    tts_provider: str | None
    tts_model: str | None
    enabled: bool
    asr_cost_micros_per_minute: int
    llm_input_cost_micros_per_million_tokens: int
    llm_output_cost_micros_per_million_tokens: int
    tts_cost_micros_per_10k_chars: int


class AdminModelPresetUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=240)
    route_kind: Literal["cascade", "realtime_s2s", "managed_app"] | None = None
    realtime_provider: str | None = Field(default=None, min_length=1, max_length=40)
    realtime_model: str | None = Field(default=None, min_length=1, max_length=120)
    asr_provider: str | None = Field(default=None, min_length=1, max_length=40)
    asr_model: str | None = Field(default=None, min_length=1, max_length=120)
    llm_provider: str | None = Field(default=None, min_length=1, max_length=40)
    llm_model: str | None = Field(default=None, min_length=1, max_length=120)
    tts_provider: str | None = Field(default=None, min_length=1, max_length=40)
    tts_model: str | None = Field(default=None, min_length=1, max_length=120)
    asr_cost_micros_per_minute: int | None = Field(default=None, ge=0)
    llm_input_cost_micros_per_million_tokens: int | None = Field(default=None, ge=0)
    llm_output_cost_micros_per_million_tokens: int | None = Field(default=None, ge=0)
    tts_cost_micros_per_10k_chars: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    is_default: bool | None = None
    confirm: bool = False

    @model_validator(mode="after")
    def require_confirmation(self) -> "AdminModelPresetUpdateRequest":
        if not self.confirm:
            raise ValueError("model routing changes require explicit confirmation")
        return self


class VoicePresetResponse(BaseModel):
    id: str
    display_name: str
    language: str
    provider: str
    voice: str
    preview_url: str | None
    is_default: bool


class UsageProfileCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    age_band: Literal["12_13", "14_17"]
    guardian_confirmed: bool

    @model_validator(mode="after")
    def require_guardian_confirmation(self) -> "UsageProfileCreateRequest":
        if not self.guardian_confirmed:
            raise ValueError("guardian confirmation is required")
        return self


class GuardianControlsRequest(BaseModel):
    memory_consent: bool | None = None
    quiet_start: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    daily_limit_minutes: int | None = Field(default=None, ge=30, le=180)
    continuous_reminder_minutes: int | None = Field(default=None, ge=15, le=60)


class UsageProfileResponse(BaseModel):
    id: str
    kind: Literal["adult", "youth"]
    display_name: str
    age_band: Literal["12_13", "14_17"] | None
    guardian_consent_version: str | None
    guardian_consent_at: datetime | None
    memory_consent: bool
    quiet_start: str
    quiet_end: str
    daily_limit_minutes: int
    continuous_reminder_minutes: int


class ActiveProfileRequest(BaseModel):
    profile_id: str
    agent_id: str | None = None


class OnboardingStatusResponse(BaseModel):
    device_bound: bool
    assistant_configured: bool
    device_online: bool
    first_conversation_complete: bool
    next_action: Literal[
        "bind_device",
        "configure_assistant",
        "bring_device_online",
        "start_conversation",
        "complete",
    ]
    active_device_id: str | None
    active_agent_id: str | None


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str


class AgentMemoryUpsertRequest(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9._-]+$")
    value: str = Field(min_length=1, max_length=2000)


class AgentMemoryResponse(BaseModel):
    id: str
    key: str
    value: str
    updated_at: datetime


class ConversationResponse(BaseModel):
    id: str
    agent_id: str
    device_id: str
    turn_count: int
    first_audio_latency_ms: int | None
    provider_cost_micros: int
    end_reason: str
    started_at: datetime
    ended_at: datetime | None
    summary: str | None = None


class SessionSummaryUpdateRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)


class MemoryExportResponse(BaseModel):
    generated_at: datetime
    agent_memories: list[dict[str, str]]
    session_summaries: list[dict[str, str]]


class UsageSummaryResponse(BaseModel):
    voice_turns: int
    provider_cost_micros: int
    pricing_configured: bool
    asr_units: int
    llm_input_units: int
    llm_output_units: int
    tts_units: int
    managed_dialog_requests: int = 0
    realtime_s2s_requests: int = 0
    unknown_cost_records: int = 0


class ProviderUsageMeter(BaseModel):
    kind: Literal["input_audio", "output_audio", "input_text", "output_text"]
    unit: Literal["milliseconds", "tokens", "characters"]
    quantity: int = Field(ge=0, strict=True)


class ProviderUsageDetails(BaseModel):
    """Only metering data belongs here, never a provider response or transcript."""

    schema_version: Literal[1] = 1
    meters: list[ProviderUsageMeter] = Field(default_factory=list, max_length=32)
    provider_usage: dict[str, object] = Field(default_factory=dict)

    @field_validator("provider_usage")
    @classmethod
    def numeric_usage_only(cls, value: dict[str, object]) -> dict[str, object]:
        count = 0

        def validate(item: object, depth: int) -> None:
            nonlocal count
            count += 1
            if count > 256 or depth > 4:
                raise ValueError("provider usage exceeds metering limits")
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str) or not re.fullmatch(
                        r"[A-Za-z][A-Za-z0-9_]{0,79}", key
                    ):
                        raise ValueError("provider usage keys must be counter names")
                    validate(child, depth + 1)
            elif type(item) not in (int, float) or not isfinite(item) or item < 0:
                raise ValueError("provider usage must contain only nonnegative numeric counters")

        validate(value, 0)
        return value


class StaffCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    role: Literal["superadmin", "engineering", "support", "factory"]


class StaffLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)


class StaffResponse(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    active: bool


class AdminDeviceResponse(BaseModel):
    id: str
    serial_number: str
    board_type: str
    lifecycle: str
    owner_user_id: str | None
    active_agent_id: str | None
    hardware_version: str
    firmware_version: str
    last_seen_at: datetime | None


class AdminUserResponse(BaseModel):
    id: str
    display_name: str
    adult_confirmed: bool
    device_count: int
    created_at: datetime


class AdminAuditResponse(BaseModel):
    id: str
    actor_type: str
    actor_id: str
    action: str
    payload: dict[str, object]
    created_at: datetime


class DeviceRmaRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=240)
    confirm: bool = False

    @model_validator(mode="after")
    def require_confirmation(self) -> "DeviceRmaRequest":
        if not self.confirm:
            raise ValueError("RMA quarantine requires explicit confirmation")
        return self


class DeviceUnbindResponse(BaseModel):
    id: str
    lifecycle: str
    reset_epoch: int


class VisionCapabilityResponse(BaseModel):
    enabled: bool = False
    reason: str = "vision is reserved but disabled for the pilot"


class FirmwareReleaseRequest(BaseModel):
    board_type: str = Field(pattern=r"^[a-z0-9.-]+$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?$")
    artifact_url: HttpUrl
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str = Field(min_length=32)
    rollout_percent: int = Field(ge=0, le=100)
    mandatory: bool = False
    active: bool = False
    confirm: bool = False

    @field_validator("artifact_url")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("firmware artifact URL must use HTTPS")
        return value

    @model_validator(mode="after")
    def require_confirmation(self) -> "FirmwareReleaseRequest":
        if not self.confirm:
            raise ValueError("OTA release requires explicit confirmation")
        return self


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
    queued: bool
