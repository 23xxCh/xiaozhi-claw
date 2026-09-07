import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..catalog import ensure_default_agent
from ..db import get_session
from ..dependencies import authenticate_device, require_admin_or_staff, require_adult_user
from ..models import (
    Agent,
    Claim,
    Device,
    DeviceCommand,
    DeviceLifecycle,
    DeviceSession,
    Entitlement,
    StaffRole,
    StaffUser,
    User,
)
from ..quota import _as_utc
from ..schemas import (
    ClaimConfirmRequest,
    DeviceBootstrapRequest,
    DeviceBootstrapResponse,
    DeviceCredentialRotationRequest,
    DeviceDetailResponse,
    DeviceRegistrationRequest,
    DeviceRegistrationResponse,
    DeviceResponse,
    DeviceUnbindResponse,
    DeviceUpdateRequest,
    MemoryConsentRequest,
)
from ..security import (
    create_device_session_token,
    hash_secret,
    new_claim_code,
    new_device_secret,
)

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


async def _device_detail_response(
    session: AsyncSession, device: Device, offline_after_seconds: int
) -> DeviceDetailResponse:
    latest = await session.scalar(
        select(DeviceSession)
        .where(DeviceSession.device_id == device.id)
        .order_by(DeviceSession.connected_at.desc(), DeviceSession.id.desc())
    )
    return _device_detail_from_session(device, latest, offline_after_seconds)


def _device_detail_from_session(
    device: Device,
    latest: DeviceSession | None,
    offline_after_seconds: int,
) -> DeviceDetailResponse:
    online = False
    if latest is not None and latest.status == "online":
        heartbeat = latest.heartbeat_at
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=UTC)
        online = heartbeat >= datetime.now(UTC) - timedelta(seconds=offline_after_seconds)
    return DeviceDetailResponse(
        **_device_response(device).model_dump(),
        name=device.name,
        hardware_version=device.hardware_version,
        ota_auto_update=device.ota_auto_update,
        active_agent_id=device.active_agent_id,
        active_profile_id=device.active_profile_id,
        online=online,
        last_seen_at=device.last_seen_at,
    )


@router.post(
    "/admin/devices",
    response_model=DeviceRegistrationResponse,
    dependencies=[Depends(require_admin_or_staff(StaffRole.SUPERADMIN, StaffRole.FACTORY))],
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


@router.post(
    "/admin/devices/{device_id}/rotate-credential",
    response_model=DeviceRegistrationResponse,
)
async def rotate_device_credential(
    device_id: str,
    payload: DeviceCredentialRotationRequest,
    request: Request,
    staff: StaffUser | None = Depends(
        require_admin_or_staff(StaffRole.SUPERADMIN, StaffRole.FACTORY)
    ),
    session: AsyncSession = Depends(get_session),
) -> DeviceRegistrationResponse:
    del payload
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")

    device_secret = new_device_secret()
    device.credential_hash = hash_secret(
        device_secret, request.app.state.settings.device_credential_pepper
    )
    add_audit_event(
        session,
        actor_type="staff" if staff is not None else "admin",
        actor_id=staff.id if staff is not None else "factory-api",
        action="device.credential-rotated",
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

    if device.lifecycle == DeviceLifecycle.OWNED.value and device.owner_user_id:
        return DeviceBootstrapResponse(
            lifecycle=device.lifecycle,
            websocket={
                "url": request.app.state.settings.device_ws_url,
                "token": create_device_session_token(
                    device.serial_number, request.app.state.settings
                ),
                "version": 1,
            },
        )

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


@router.post("/claims/confirm", response_model=DeviceResponse)
@router.post("/claims/confirm-phone", response_model=DeviceResponse, include_in_schema=False)
async def confirm_claim(
    payload: ClaimConfirmRequest,
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceResponse:
    if not user.adult_confirmed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="18+ confirmation required"
        )
    # Authentication only reads. Start a fresh transaction so a concurrent claim's
    # account changes are visible after acquiring the lock (including MySQL RR).
    user_id = user.id
    await session.rollback()
    if session.get_bind().dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE; a no-op write serializes local claim transactions.
        await session.execute(update(User).where(User.id == user_id).values(id=User.id))
    await session.refresh(user, with_for_update=True)
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
    agent = await ensure_default_agent(session, user)
    device.active_agent_id = agent.id
    device.active_profile_id = agent.usage_profile_id
    if device.service_gift_status == "eligible":
        entitlement = await session.scalar(
            select(Entitlement)
            .where(Entitlement.user_id == user.id, Entitlement.expires_at > now)
            .order_by(Entitlement.expires_at.desc())
            .limit(1)
            .with_for_update()
        )
        gift_starts_at = _as_utc(entitlement.expires_at) if entitlement else now
        gift_expires_at = gift_starts_at + timedelta(days=request.app.state.settings.trial_days)
        if entitlement is None:
            entitlement = Entitlement(
                user_id=user.id,
                plan="trial",
                monthly_turn_limit=request.app.state.settings.trial_monthly_turns,
                starts_at=now,
                expires_at=gift_expires_at,
            )
            session.add(entitlement)
        else:
            entitlement.expires_at = gift_expires_at
        device.service_gift_status = "granted"
        device.service_gift_granted_at = now
        await session.flush()
        add_audit_event(
            session,
            actor_type="user",
            actor_id=user.id,
            action="device.service-gift-granted",
            payload={
                "device_id": device.id,
                "claim_id": claim.id,
                "entitlement_id": entitlement.id,
                "starts_at": gift_starts_at.isoformat(),
                "expires_at": gift_expires_at.isoformat(),
            },
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


@router.get("/devices", response_model=list[DeviceDetailResponse])
async def list_devices(
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> list[DeviceDetailResponse]:
    devices = list(await session.scalars(select(Device).where(Device.owner_user_id == user.id)))
    if not devices:
        return []
    ranked_sessions = (
        select(
            DeviceSession.id.label("id"),
            func.row_number()
            .over(
                partition_by=DeviceSession.device_id,
                order_by=(DeviceSession.connected_at.desc(), DeviceSession.id.desc()),
            )
            .label("session_rank"),
        )
        .where(DeviceSession.device_id.in_([device.id for device in devices]))
        .subquery()
    )
    latest_sessions = list(
        await session.scalars(
            select(DeviceSession)
            .join(ranked_sessions, DeviceSession.id == ranked_sessions.c.id)
            .where(ranked_sessions.c.session_rank == 1)
        )
    )
    sessions_by_device = {item.device_id: item for item in latest_sessions}
    return [
        _device_detail_from_session(
            device,
            sessions_by_device.get(device.id),
            request.app.state.settings.device_offline_after_seconds,
        )
        for device in devices
    ]


@router.patch("/devices/{device_id}", response_model=DeviceDetailResponse)
async def update_device(
    device_id: str,
    payload: DeviceUpdateRequest,
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceDetailResponse:
    device = await session.get(Device, device_id)
    if device is None or device.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    if payload.name is not None:
        device.name = payload.name
    if payload.ota_auto_update is not None:
        device.ota_auto_update = payload.ota_auto_update
    if payload.active_agent_id is not None:
        agent = await session.get(Agent, payload.active_agent_id)
        if agent is None or agent.owner_user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
        device.active_agent_id = agent.id
        device.active_profile_id = agent.usage_profile_id
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="device.updated",
        payload={"device_id": device.id},
    )
    await session.commit()
    return await _device_detail_response(
        session, device, request.app.state.settings.device_offline_after_seconds
    )


@router.post("/devices/{device_id}/unbind", response_model=DeviceUnbindResponse)
async def unbind_device(
    device_id: str,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceUnbindResponse:
    device = await session.get(Device, device_id)
    if device is None or device.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    device.owner_user_id = None
    device.active_agent_id = None
    device.active_profile_id = None
    device.lifecycle = DeviceLifecycle.FACTORY_UNCLAIMED.value
    device.memory_consent = False
    device.reset_epoch += 1
    session.add(
        DeviceCommand(
            device_id=device.id,
            command_type="ownership-revoked",
            payload_json=json.dumps({"reset_epoch": device.reset_epoch}),
        )
    )
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="device.unbound",
        payload={"device_id": device.id, "reset_epoch": device.reset_epoch},
    )
    await session.commit()
    return DeviceUnbindResponse(
        id=device.id,
        lifecycle=device.lifecycle,
        reset_epoch=device.reset_epoch,
    )


@router.patch("/devices/{device_id}/memory-consent", response_model=DeviceResponse)
async def set_memory_consent(
    device_id: str,
    payload: MemoryConsentRequest,
    user: User = Depends(require_adult_user),
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
