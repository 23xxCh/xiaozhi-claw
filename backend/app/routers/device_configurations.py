import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.generated.device_contracts import (
    DEVICE_CONFIG_FIELDS,
    DEVICE_CONFIG_SCHEMA_VERSION,
    default_device_config,
    validate_device_config,
)

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import require_adult_user, require_staff
from ..models import (
    Device,
    DeviceCommand,
    DeviceCommandStatus,
    DeviceConfiguration,
    StaffRole,
    StaffUser,
    User,
)
from ..schemas import (
    DeviceConfigurationFieldResponse,
    DeviceConfigurationResponse,
    DeviceConfigurationSchemaResponse,
    DeviceConfigurationUpdateRequest,
)

router = APIRouter(prefix="/v1/devices", tags=["device-configuration"])
admin_router = APIRouter(prefix="/v1/admin/devices", tags=["admin-device-configuration"])


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


def _desired_values(configuration: DeviceConfiguration) -> dict[str, int | str]:
    values = default_device_config()
    values.update(configuration.desired_values or {})
    if not configuration.desired_values:
        values["audio.speaker_volume"] = configuration.speaker_volume
        values["display.brightness"] = configuration.screen_brightness
        configuration.desired_values = values
    return values


def _configuration_schema(*permissions: str) -> DeviceConfigurationSchemaResponse:
    fields = [
        DeviceConfigurationFieldResponse(**spec)
        for spec in DEVICE_CONFIG_FIELDS.values()
        if spec["permission"] in permissions
    ]
    return DeviceConfigurationSchemaResponse(
        schema_version=DEVICE_CONFIG_SCHEMA_VERSION,
        fields=fields,
    )


def _response(
    configuration: DeviceConfiguration, command_id: str | None = None
) -> DeviceConfigurationResponse:
    values = _desired_values(configuration)
    applied_values = configuration.applied_values
    if (
        configuration.last_error_code
        and configuration.applied_version < configuration.desired_version
    ):
        sync_status = "failed"
    elif configuration.applied_version < configuration.desired_version:
        sync_status = "pending"
    elif not applied_values and configuration.applied_speaker_volume is None:
        sync_status = "unknown"
    else:
        sync_status = "synced"
    return DeviceConfigurationResponse(
        device_id=configuration.device_id,
        desired_version=configuration.desired_version,
        applied_version=configuration.applied_version,
        schema_version=configuration.schema_version,
        values=values,
        applied_values=applied_values,
        speaker_volume=int(values["audio.speaker_volume"]),
        screen_brightness=int(values["display.brightness"]),
        applied_speaker_volume=configuration.applied_speaker_volume,
        applied_screen_brightness=configuration.applied_screen_brightness,
        sync_status=sync_status,
        last_error_code=configuration.last_error_code,
        command_id=command_id,
        updated_at=configuration.updated_at,
        applied_at=configuration.applied_at,
    )


def _payload_values(payload: DeviceConfigurationUpdateRequest) -> dict[str, int | str]:
    values = dict(payload.values or {})
    if payload.speaker_volume is not None:
        values["audio.speaker_volume"] = payload.speaker_volume
    if payload.screen_brightness is not None:
        values["display.brightness"] = payload.screen_brightness
    return values


async def _update_configuration(
    *,
    device: Device,
    payload: DeviceConfigurationUpdateRequest,
    request: Request,
    session: AsyncSession,
    permissions: set[str],
    actor_type: str,
    actor_id: str,
) -> DeviceConfigurationResponse:
    if payload.schema_version != DEVICE_CONFIG_SCHEMA_VERSION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="unsupported device configuration schema version",
        )
    try:
        patch = validate_device_config(
            _payload_values(payload), allowed_permissions=permissions
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    configuration = await _configuration(session, device.id)
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

    values = _desired_values(configuration)
    values.update(patch)
    configuration.schema_version = DEVICE_CONFIG_SCHEMA_VERSION
    configuration.desired_values = values
    configuration.speaker_volume = int(values["audio.speaker_volume"])
    configuration.screen_brightness = int(values["display.brightness"])
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
        "schema_version": DEVICE_CONFIG_SCHEMA_VERSION,
        "values": values,
        # Retained until all pilot devices advertise device-config/v1.
        "config": {
            "speaker_volume": configuration.speaker_volume,
            "screen_brightness": configuration.screen_brightness,
        },
    }
    command.payload_json = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    delivered = await request.app.state.device_connections.send_json(
        device.serial_number, message
    )
    if delivered:
        command.status = DeviceCommandStatus.DELIVERED.value
        command.delivered_at = datetime.now(UTC)

    add_audit_event(
        session,
        actor_type=actor_type,
        actor_id=actor_id,
        action="device.configuration-updated",
        payload={
            "device_id": device.id,
            "config_version": configuration.desired_version,
            "changed_fields": sorted(patch),
        },
    )
    await session.commit()
    return _response(configuration, command.id)


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


@router.get(
    "/{device_id}/configuration-schema",
    response_model=DeviceConfigurationSchemaResponse,
)
async def get_device_configuration_schema(
    device_id: str,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceConfigurationSchemaResponse:
    await _owned_device(session, user, device_id)
    return _configuration_schema("customer")


@router.patch("/{device_id}/configuration", response_model=DeviceConfigurationResponse)
async def update_device_configuration(
    device_id: str,
    payload: DeviceConfigurationUpdateRequest,
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceConfigurationResponse:
    device = await _owned_device(session, user, device_id)
    return await _update_configuration(
        device=device,
        payload=payload,
        request=request,
        session=session,
        permissions={"customer"},
        actor_type="user",
        actor_id=user.id,
    )


@admin_router.get(
    "/{device_id}/configuration-schema",
    response_model=DeviceConfigurationSchemaResponse,
)
async def admin_device_configuration_schema(
    device_id: str,
    _: StaffUser = Depends(require_staff(StaffRole.SUPERADMIN, StaffRole.ENGINEERING)),
    session: AsyncSession = Depends(get_session),
) -> DeviceConfigurationSchemaResponse:
    if await session.get(Device, device_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    return _configuration_schema("customer", "engineering")


@admin_router.patch(
    "/{device_id}/configuration",
    response_model=DeviceConfigurationResponse,
)
async def admin_update_device_configuration(
    device_id: str,
    payload: DeviceConfigurationUpdateRequest,
    request: Request,
    staff: StaffUser = Depends(require_staff(StaffRole.SUPERADMIN, StaffRole.ENGINEERING)),
    session: AsyncSession = Depends(get_session),
) -> DeviceConfigurationResponse:
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    return await _update_configuration(
        device=device,
        payload=payload,
        request=request,
        session=session,
        permissions={"customer", "engineering"},
        actor_type="staff",
        actor_id=staff.id,
    )
