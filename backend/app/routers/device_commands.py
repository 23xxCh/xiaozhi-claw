import json
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import require_adult_user
from ..models import Device, DeviceCommand, DeviceCommandStatus, User
from ..schemas import DeviceCommandResponse

router = APIRouter(prefix="/v1/devices", tags=["device-commands"])


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _owned_device(session: AsyncSession, user: User, device_id: str) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    return device


def _response(command: DeviceCommand) -> DeviceCommandResponse:
    return DeviceCommandResponse(
        command_id=command.id,
        device_id=command.device_id,
        status=cast(
            Literal["pending", "delivered", "applied", "failed", "expired"],
            command.status,
        ),
        error_code=command.error_code,
        created_at=command.created_at,
        expires_at=command.expires_at,
        delivered_at=command.delivered_at,
        applied_at=command.applied_at,
    )


@router.post(
    "/{device_id}/standby",
    response_model=DeviceCommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enter_device_standby(
    device_id: str,
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceCommandResponse:
    device = await _owned_device(session, user, device_id)
    now = datetime.now(UTC)
    command = DeviceCommand(
        device_id=device.id,
        command_type="enter-standby",
        payload_json="{}",
        expires_at=now + timedelta(seconds=10),
    )
    session.add(command)
    await session.flush()
    message: dict[str, object] = {
        "type": "system",
        "command": "enter_standby",
        "command_id": command.id,
    }
    command.payload_json = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    if await request.app.state.device_connections.send_json(device.serial_number, message):
        command.status = DeviceCommandStatus.DELIVERED.value
        command.delivered_at = now
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="device.standby-requested",
        payload={"device_id": device.id, "command_id": command.id},
    )
    await session.commit()
    return _response(command)


@router.get(
    "/{device_id}/commands/{command_id}", response_model=DeviceCommandResponse
)
async def get_device_command(
    device_id: str,
    command_id: str,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceCommandResponse:
    await _owned_device(session, user, device_id)
    command = await session.get(DeviceCommand, command_id)
    if command is None or command.device_id != device_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="command not found")
    if (
        command.status in {
            DeviceCommandStatus.PENDING.value,
            DeviceCommandStatus.DELIVERED.value,
        }
        and command.expires_at is not None
        and _as_utc(command.expires_at) <= datetime.now(UTC)
    ):
        command.status = DeviceCommandStatus.EXPIRED.value
        command.error_code = "command-expired"
        await session.commit()
    return _response(command)
