import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import require_adult_user
from ..models import (
    Device,
    DeviceCommand,
    DeviceCommandStatus,
    DeviceConfiguration,
    User,
)
from ..schemas import DeviceConfigurationResponse, DeviceConfigurationUpdateRequest

router = APIRouter(prefix="/v1/devices", tags=["device-configuration"])


async def _owned_device(session: AsyncSession, user: User, device_id: str) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    return device


async def _configuration(session: AsyncSession, device_id: str) -> DeviceConfiguration:
    configuration = await session.get(DeviceConfiguration, device_id)
    if configuration is None:
        configuration = DeviceConfiguration(device_id=device_id)
        session.add(configuration)
        await session.flush()
    return configuration


def _response(
    configuration: DeviceConfiguration, command_id: str | None = None
) -> DeviceConfigurationResponse:
    if (
        configuration.last_error_code
        and configuration.applied_version < configuration.desired_version
    ):
        sync_status = "failed"
    elif configuration.applied_version < configuration.desired_version:
        sync_status = "pending"
    elif configuration.applied_speaker_volume is None:
        sync_status = "unknown"
    else:
        sync_status = "synced"
    return DeviceConfigurationResponse(
        device_id=configuration.device_id,
        desired_version=configuration.desired_version,
        applied_version=configuration.applied_version,
        speaker_volume=configuration.speaker_volume,
        screen_brightness=configuration.screen_brightness,
        applied_speaker_volume=configuration.applied_speaker_volume,
        applied_screen_brightness=configuration.applied_screen_brightness,
        sync_status=sync_status,
        last_error_code=configuration.last_error_code,
        command_id=command_id,
        updated_at=configuration.updated_at,
        applied_at=configuration.applied_at,
    )


@router.get("/{device_id}/configuration", response_model=DeviceConfigurationResponse)
async def get_device_configuration(
    device_id: str,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceConfigurationResponse:
    await _owned_device(session, user, device_id)
    configuration = await _configuration(session, device_id)
    await session.commit()
    return _response(configuration)


@router.patch("/{device_id}/configuration", response_model=DeviceConfigurationResponse)
async def update_device_configuration(
    device_id: str,
    payload: DeviceConfigurationUpdateRequest,
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceConfigurationResponse:
    device = await _owned_device(session, user, device_id)
    configuration = await _configuration(session, device_id)

    older_commands = list(
        await session.scalars(
            select(DeviceCommand).where(
                DeviceCommand.device_id == device.id,
                DeviceCommand.command_type == "device-config",
                DeviceCommand.status.in_(
                    [DeviceCommandStatus.PENDING.value, DeviceCommandStatus.DELIVERED.value]
                ),
            )
        )
    )
    for older in older_commands:
        older.status = DeviceCommandStatus.EXPIRED.value
        older.error_code = "superseded"

    configuration.speaker_volume = payload.speaker_volume
    configuration.screen_brightness = payload.screen_brightness
    configuration.desired_version += 1
    configuration.last_error_code = None
    configuration.updated_at = datetime.now(UTC)

    command = DeviceCommand(
        device_id=device.id,
        command_type="device-config",
        payload_json="{}",
    )
    session.add(command)
    await session.flush()
    message: dict[str, object] = {
        "type": "system",
        "command": "apply_config",
        "command_id": command.id,
        "config_version": configuration.desired_version,
        "config": {
            "speaker_volume": configuration.speaker_volume,
            "screen_brightness": configuration.screen_brightness,
        },
    }
    command.payload_json = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    delivered = await request.app.state.device_connections.send_json(device.serial_number, message)
    if delivered:
        command.status = DeviceCommandStatus.DELIVERED.value
        command.delivered_at = datetime.now(UTC)

    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="device.configuration-updated",
        payload={
            "device_id": device.id,
            "config_version": configuration.desired_version,
            "speaker_volume": configuration.speaker_volume,
            "screen_brightness": configuration.screen_brightness,
        },
    )
    await session.commit()
    return _response(configuration, command.id)
