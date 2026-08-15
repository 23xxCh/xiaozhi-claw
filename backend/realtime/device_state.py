import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.audit import add_audit_event
from backend.app.models import (
    Device,
    DeviceCommand,
    DeviceCommandStatus,
    DeviceConfiguration,
    DeviceSession,
)
from backend.generated.device_contracts import (
    DEVICE_CONFIG_SCHEMA_VERSION,
    validate_device_config,
)

logger = logging.getLogger(__name__)


async def heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    device_session_id: str,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=30)
        if stop_event.is_set():
            return
        async with session_factory() as session:
            device_session = await session.get(DeviceSession, device_session_id)
            if device_session is None:
                return
            device_session.heartbeat_at = datetime.now(UTC)
            await session.commit()


async def handle_device_config_ack(
    session_factory: async_sessionmaker[AsyncSession],
    device_id: str,
    message: dict[str, object],
) -> None:
    command_id = message.get("command_id")
    config_version = message.get("config_version")
    ack_status = message.get("status")
    schema_version = message.get("schema_version", DEVICE_CONFIG_SCHEMA_VERSION)
    applied_values = message.get("applied_values")
    legacy_applied = message.get("applied")
    if (
        not isinstance(command_id, str)
        or not isinstance(config_version, int)
        or isinstance(config_version, bool)
        or not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or ack_status not in {"applied", "failed"}
    ):
        logger.warning("ignored malformed device configuration acknowledgement")
        return

    async with session_factory() as session:
        command = await session.get(DeviceCommand, command_id)
        configuration = await session.get(DeviceConfiguration, device_id)
        if (
            command is None
            or command.device_id != device_id
            or command.command_type != "device-config"
            or configuration is None
        ):
            logger.warning("ignored unknown device configuration acknowledgement %s", command_id)
            return
        try:
            command_payload = json.loads(command.payload_json)
            expected_version = int(command_payload["config_version"])
            expected_schema_version = int(
                command_payload.get("schema_version", DEVICE_CONFIG_SCHEMA_VERSION)
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            command.status = DeviceCommandStatus.FAILED.value
            command.error_code = "invalid-command-payload"
            await session.commit()
            return
        if config_version != expected_version:
            command.status = DeviceCommandStatus.FAILED.value
            command.error_code = "ack-version-mismatch"
            await session.commit()
            return
        if schema_version != expected_schema_version:
            command.status = DeviceCommandStatus.FAILED.value
            command.error_code = "ack-schema-version-mismatch"
            await session.commit()
            return

        now = datetime.now(UTC)
        if ack_status == "failed":
            error_code = message.get("error_code")
            command.status = DeviceCommandStatus.FAILED.value
            command.error_code = (
                error_code[:80]
                if isinstance(error_code, str) and error_code
                else "device-rejected"
            )
            if config_version == configuration.desired_version:
                configuration.last_error_code = command.error_code
            await session.commit()
            return

        if applied_values is None and isinstance(legacy_applied, dict):
            legacy_values: dict[str, object] = {}
            if "speaker_volume" in legacy_applied:
                legacy_values["audio.speaker_volume"] = legacy_applied["speaker_volume"]
            if "screen_brightness" in legacy_applied:
                legacy_values["display.brightness"] = legacy_applied["screen_brightness"]
            applied_values = legacy_values
        if not isinstance(applied_values, dict):
            command.status = DeviceCommandStatus.FAILED.value
            command.error_code = "invalid-ack-payload"
            if config_version == configuration.desired_version:
                configuration.last_error_code = command.error_code
            await session.commit()
            return
        try:
            validated_values = validate_device_config(cast(dict[str, object], applied_values))
        except (PermissionError, ValueError):
            command.status = DeviceCommandStatus.FAILED.value
            command.error_code = "invalid-ack-values"
            if config_version == configuration.desired_version:
                configuration.last_error_code = command.error_code
            await session.commit()
            return

        command.status = DeviceCommandStatus.APPLIED.value
        command.applied_at = now
        command.error_code = None
        if config_version >= configuration.applied_version:
            configuration.applied_version = config_version
            configuration.schema_version = schema_version
            configuration.applied_values = cast(dict[str, object], validated_values)
            speaker_volume = validated_values.get("audio.speaker_volume")
            screen_brightness = validated_values.get("display.brightness")
            if isinstance(speaker_volume, int):
                configuration.applied_speaker_volume = speaker_volume
            if isinstance(screen_brightness, int):
                configuration.applied_screen_brightness = screen_brightness
            configuration.applied_at = now
        if config_version == configuration.desired_version:
            configuration.last_error_code = None
        add_audit_event(
            session,
            actor_type="device",
            actor_id=device_id,
            action="device.configuration-applied",
            payload={"command_id": command.id, "config_version": config_version},
        )
        await session.commit()


async def record_device_hello(
    session_factory: async_sessionmaker[AsyncSession],
    device_id: str,
    message: dict[str, object],
) -> None:
    text_fields = {
        "hardware_profile_id": 80,
        "display_profile_id": 80,
        "profile_sha256": 64,
    }
    async with session_factory() as session:
        device = await session.get(Device, device_id)
        if device is None:
            return
        for field, max_length in text_fields.items():
            value = message.get(field)
            if isinstance(value, str) and value and len(value) <= max_length:
                setattr(device, field, value)
        for field in ("profile_schema_version", "device_config_schema_version"):
            value = message.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                setattr(device, field, value)
        await session.commit()
