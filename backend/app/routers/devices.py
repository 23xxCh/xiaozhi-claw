from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import authenticate_device, require_admin, require_user
from ..models import Claim, Device, DeviceLifecycle, Entitlement, User
from ..schemas import (
    ClaimConfirmRequest,
    DeviceBootstrapRequest,
    DeviceBootstrapResponse,
    DeviceRegistrationRequest,
    DeviceRegistrationResponse,
    DeviceResponse,
    MemoryConsentRequest,
)
from ..security import hash_secret, new_claim_code, new_device_secret

router = APIRouter(prefix="/v1", tags=["devices"])


def _device_response(device: Device) -> DeviceResponse:
    return DeviceResponse(
        id=device.id,
        serial_number=device.serial_number,
        board_type=device.board_type,
        lifecycle=device.lifecycle,
        memory_consent=device.memory_consent,
        firmware_version=device.firmware_version,
    )


@router.post(
    "/admin/devices",
    response_model=DeviceRegistrationResponse,
    dependencies=[Depends(require_admin)],
)
async def register_device(
    payload: DeviceRegistrationRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> DeviceRegistrationResponse:
    existing = await session.scalar(
        select(Device).where(Device.serial_number == payload.serial_number)
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="serial already exists")

    device_secret = new_device_secret()
    device = Device(
        serial_number=payload.serial_number,
        board_type=payload.board_type,
        credential_hash=hash_secret(
            device_secret, request.app.state.settings.device_credential_pepper
        ),
    )
    session.add(device)
    await session.flush()
    add_audit_event(
        session,
        actor_type="admin",
        actor_id="factory-api",
        action="device.registered",
        payload={"device_id": device.id, "serial_number": device.serial_number},
    )
    await session.commit()
    return DeviceRegistrationResponse(
        device_id=device.id,
        serial_number=device.serial_number,
        device_secret=device_secret,
        lifecycle=device.lifecycle,
    )


@router.post("/device/bootstrap", response_model=DeviceBootstrapResponse)
async def bootstrap_device(
    payload: DeviceBootstrapRequest,
    request: Request,
    device_id: str = Header(alias="Device-Id"),
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> DeviceBootstrapResponse:
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing device secret"
        )
    device = await authenticate_device(
        request, session, device_id, authorization.removeprefix("Bearer ")
    )
    if device.lifecycle in {
        DeviceLifecycle.LOST_LOCKED.value,
        DeviceLifecycle.RMA_QUARANTINE.value,
        DeviceLifecycle.SERVICE_LOCKED.value,
        DeviceLifecycle.RETIRED.value,
    }:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=device.lifecycle)

    now = datetime.now(UTC)
    code = new_claim_code()
    claim = Claim(
        code_hash=hash_secret(code, request.app.state.settings.device_credential_pepper),
        device_id=device.id,
        expires_at=now + timedelta(seconds=request.app.state.settings.claim_ttl_seconds),
    )
    device.firmware_version = payload.firmware_version
    device.last_seen_at = now
    session.add(claim)
    add_audit_event(
        session,
        actor_type="device",
        actor_id=device.id,
        action="claim.device-bootstrap",
        payload={"claim_id": claim.id, "lifecycle": device.lifecycle},
    )
    await session.commit()
    return DeviceBootstrapResponse(
        claim_code=code, expires_at=claim.expires_at, lifecycle=device.lifecycle
    )


@router.post("/claims/confirm-phone", response_model=DeviceResponse)
async def confirm_claim(
    payload: ClaimConfirmRequest,
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceResponse:
    if not user.adult_confirmed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="18+ confirmation required"
        )
    code_hash = hash_secret(payload.claim_code, request.app.state.settings.device_credential_pepper)
    claim = await session.scalar(
        select(Claim).where(Claim.code_hash == code_hash).with_for_update()
    )
    now = datetime.now(UTC)
    if claim is None or claim.consumed_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")
    expires_at = claim.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="claim expired")

    device = await session.get(Device, claim.device_id, with_for_update=True)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    if device.lifecycle != DeviceLifecycle.FACTORY_UNCLAIMED.value or device.owner_user_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="device already owned")

    device.owner_user_id = user.id
    device.lifecycle = DeviceLifecycle.OWNED.value
    claim.consumed_at = now
    session.add(
        Entitlement(
            user_id=user.id,
            plan="trial",
            monthly_turn_limit=request.app.state.settings.trial_monthly_turns,
            starts_at=now,
            expires_at=now + timedelta(days=request.app.state.settings.trial_days),
        )
    )
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="device.claimed",
        payload={"device_id": device.id, "claim_id": claim.id},
    )
    await session.commit()
    return _device_response(device)


@router.get("/devices", response_model=list[DeviceResponse])
async def list_devices(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[DeviceResponse]:
    devices = list(await session.scalars(select(Device).where(Device.owner_user_id == user.id)))
    return [_device_response(device) for device in devices]


@router.patch("/devices/{device_id}/memory-consent", response_model=DeviceResponse)
async def set_memory_consent(
    device_id: str,
    payload: MemoryConsentRequest,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceResponse:
    device = await session.get(Device, device_id)
    if device is None or device.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    device.memory_consent = payload.enabled
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="memory.consent-updated",
        payload={"device_id": device.id, "enabled": payload.enabled},
    )
    await session.commit()
    return _device_response(device)
