import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class DeviceLifecycle(StrEnum):
    FACTORY_UNCLAIMED = "factory-unclaimed"
    OWNED = "owned"
    OWNED_RECOVERY_REQUIRED = "owned-recovery-required"
    TRANSFER_QUARANTINE = "transfer-quarantine"
    LOST_LOCKED = "lost-locked"
    RMA_QUARANTINE = "rma-quarantine"
    SERVICE_LOCKED = "service-locked"
    RETIRED = "retired"


class StaffRole(StrEnum):
    SUPERADMIN = "superadmin"
    ENGINEERING = "engineering"
    SUPPORT = "support"
    FACTORY = "factory"


class DeviceCommandStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    APPLIED = "applied"
    FAILED = "failed"
    EXPIRED = "expired"


class DeviceSessionStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"


class UsageProfileKind(StrEnum):
    ADULT = "adult"
    YOUTH = "youth"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    wechat_openid: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True, nullable=True
    )
    wechat_unionid: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True, nullable=True
    )
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True, nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    display_name: Mapped[str] = mapped_column(String(80), default="Hensun 用户")
    adult_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    terms_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    privacy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_disclosure_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    devices: Mapped[list["Device"]] = relationship(back_populates="owner")
    agents: Mapped[list["Agent"]] = relationship(back_populates="owner")
    usage_profiles: Mapped[list["UsageProfile"]] = relationship(back_populates="owner")


class EmailLoginChallenge(Base):
    __tablename__ = "email_login_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(320), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    request_ip_hash: Mapped[str] = mapped_column(String(64), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class UsageProfile(Base):
    __tablename__ = "usage_profiles"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "kind", "display_name", name="uq_profile_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default=UsageProfileKind.ADULT.value, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    age_band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    guardian_consent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    guardian_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    memory_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_start_minute: Mapped[int] = mapped_column(Integer, default=22 * 60)
    quiet_end_minute: Mapped[int] = mapped_column(Integer, default=7 * 60)
    daily_limit_minutes: Mapped[int] = mapped_column(Integer, default=90)
    continuous_reminder_minutes: Mapped[int] = mapped_column(Integer, default=30)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    owner: Mapped[User] = relationship(back_populates="usage_profiles")
    agents: Mapped[list["Agent"]] = relationship(back_populates="usage_profile")
    active_devices: Mapped[list["Device"]] = relationship(
        back_populates="active_profile", foreign_keys="Device.active_profile_id"
    )


class ModelPreset(Base):
    __tablename__ = "model_presets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(240), default="")
    asr_provider: Mapped[str] = mapped_column(String(40))
    asr_model: Mapped[str] = mapped_column(String(120))
    llm_provider: Mapped[str] = mapped_column(String(40))
    llm_model: Mapped[str] = mapped_column(String(120))
    tts_provider: Mapped[str] = mapped_column(String(40))
    tts_model: Mapped[str] = mapped_column(String(120))
    asr_cost_micros_per_minute: Mapped[int] = mapped_column(Integer, default=0)
    llm_input_cost_micros_per_million_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_output_cost_micros_per_million_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tts_cost_micros_per_10k_chars: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class VoicePreset(Base):
    __tablename__ = "voice_presets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    language: Mapped[str] = mapped_column(String(32), default="zh-CN")
    provider: Mapped[str] = mapped_column(String(40), default="dashscope")
    voice: Mapped[str] = mapped_column(String(120))
    preview_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    usage_profile_id: Mapped[str] = mapped_column(ForeignKey("usage_profiles.id"), index=True)
    name: Mapped[str] = mapped_column(String(80), default="我的助手")
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    system_prompt: Mapped[str] = mapped_column(
        Text, default="你是 Hensun Desk，一位自然、可靠的桌面 AI 助手。"
    )
    model_preset_id: Mapped[str] = mapped_column(
        ForeignKey("model_presets.id"), default="fast-chat"
    )
    voice_preset_id: Mapped[str] = mapped_column(ForeignKey("voice_presets.id"), default="cherry")
    memory_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    tools_json: Mapped[str] = mapped_column(Text, default="{}")
    llm_temperature: Mapped[float] = mapped_column(Float, default=0.6)
    tts_speech_rate: Mapped[float] = mapped_column(Float, default=1.0)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    owner: Mapped[User] = relationship(back_populates="agents")
    usage_profile: Mapped[UsageProfile] = relationship(back_populates="agents")
    devices: Mapped[list["Device"]] = relationship(
        back_populates="active_agent", foreign_keys="Device.active_agent_id"
    )


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    serial_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    credential_hash: Mapped[str] = mapped_column(String(64))
    board_type: Mapped[str] = mapped_column(String(64), default="hensun-desk-v1")
    lifecycle: Mapped[str] = mapped_column(
        String(40), default=DeviceLifecycle.FACTORY_UNCLAIMED.value, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    active_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True, index=True
    )
    active_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("usage_profiles.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(80), default="Hensun Desk")
    hardware_version: Mapped[str] = mapped_column(String(32), default="v1")
    ota_auto_update: Mapped[bool] = mapped_column(Boolean, default=True)
    memory_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    firmware_version: Mapped[str] = mapped_column(String(32), default="0.0.0")
    hardware_profile_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    display_profile_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    profile_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_config_schema_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1"
    )
    reset_epoch: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped[User | None] = relationship(back_populates="devices")
    active_agent: Mapped[Agent | None] = relationship(
        back_populates="devices", foreign_keys=[active_agent_id]
    )
    active_profile: Mapped[UsageProfile | None] = relationship(
        back_populates="active_devices", foreign_keys=[active_profile_id]
    )


class DeviceConfiguration(Base):
    __tablename__ = "device_configurations"

    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    desired_values: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, server_default="{}"
    )
    applied_values: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    desired_version: Mapped[int] = mapped_column(Integer, default=0)
    applied_version: Mapped[int] = mapped_column(Integer, default=0)
    speaker_volume: Mapped[int] = mapped_column(Integer, default=70)
    screen_brightness: Mapped[int] = mapped_column(Integer, default=75)
    applied_speaker_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied_screen_brightness: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Entitlement(Base):
    __tablename__ = "entitlements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    plan: Mapped[str] = mapped_column(String(32), default="trial")
    monthly_turn_limit: Mapped[int] = mapped_column(Integer, default=600)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="voice-turn")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    provider_cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class MemorySummary(Base):
    __tablename__ = "memory_summaries"
    __table_args__ = (UniqueConstraint("device_id", "key", name="uq_device_memory_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    encrypted_value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class FirmwareRelease(Base):
    __tablename__ = "firmware_releases"
    __table_args__ = (UniqueConstraint("board_type", "version", name="uq_board_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    board_type: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(32))
    artifact_url: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(Text)
    rollout_percent: Mapped[int] = mapped_column(Integer, default=0)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(80), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentMemory(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (UniqueConstraint("agent_id", "key", name="uq_agent_memory_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    encrypted_value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    usage_profile_id: Mapped[str] = mapped_column(ForeignKey("usage_profiles.id"), index=True)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    first_audio_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    end_reason: Mapped[str] = mapped_column(String(64), default="normal")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EncryptedSessionSummary(Base):
    __tablename__ = "encrypted_session_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.id"), unique=True, index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    encrypted_summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderUsage(Base):
    __tablename__ = "provider_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversation_sessions.id"), nullable=True, index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    operation: Mapped[str] = mapped_column(String(24))
    input_units: Mapped[int] = mapped_column(Integer, default=0)
    output_units: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeviceSession(Base):
    __tablename__ = "device_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    gateway_id: Mapped[str] = mapped_column(String(80), index=True)
    connection_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default=DeviceSessionStatus.ONLINE.value)
    firmware_version: Mapped[str] = mapped_column(String(32), default="0.0.0")
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeviceCommand(Base):
    __tablename__ = "device_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    command_type: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default=DeviceCommandStatus.PENDING.value, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class StaffUser(Base):
    __tablename__ = "staff_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(24), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
