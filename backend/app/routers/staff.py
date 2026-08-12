import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import require_admin, require_staff
from ..models import (
    AuditEvent,
    Device,
    DeviceLifecycle,
    ProviderUsage,
    StaffRole,
    StaffUser,
    User,
)
from ..schemas import (
    AdminAuditResponse,
    AdminDeviceResponse,
    AdminUserResponse,
    DeviceBatchRegistrationRequest,
    DeviceRegistrationResponse,
    DeviceRmaRequest,
    StaffCreateRequest,
    StaffLoginRequest,
    StaffResponse,
    TokenResponse,
)
from ..security import create_staff_access_token, hash_secret, new_device_secret

router = APIRouter(prefix="/v1/admin", tags=["staff"])


def _response(staff: StaffUser) -> StaffResponse:
    return StaffResponse(
        id=staff.id,
        username=staff.username,
        display_name=staff.display_name,
        role=staff.role,
        active=staff.active,
    )


@router.post("/staff", response_model=StaffResponse, dependencies=[Depends(require_admin)])
async def create_staff(
    payload: StaffCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> StaffResponse:
    existing = await session.scalar(select(StaffUser).where(StaffUser.username == payload.username))
    if existing is not None:
        existing.display_name = payload.display_name
        existing.role = payload.role
        existing.active = True
        staff = existing
    else:
        staff = StaffUser(
            username=payload.username,
            display_name=payload.display_name,
            role=payload.role,
        )
        session.add(staff)
        await session.flush()
    add_audit_event(
        session,
        actor_type="admin",
        actor_id="bootstrap-admin",
        action="staff.upserted",
        payload={"staff_id": staff.id, "role": staff.role},
    )
    await session.commit()
    return _response(staff)


@router.get("/staff", response_model=list[StaffResponse], dependencies=[Depends(require_admin)])
async def list_staff(session: AsyncSession = Depends(get_session)) -> list[StaffResponse]:
    staff = list(await session.scalars(select(StaffUser).order_by(StaffUser.username)))
    return [_response(item) for item in staff]


@router.post("/auth/login", response_model=TokenResponse, dependencies=[Depends(require_admin)])
async def staff_login(
    payload: StaffLoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    staff = await session.scalar(
        select(StaffUser).where(StaffUser.username == payload.username, StaffUser.active.is_(True))
    )
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="staff not found")
    token = create_staff_access_token(staff.id, staff.role, request.app.state.settings)
    response.set_cookie(
        "hensun_staff_session",
        token,
        httponly=True,
        secure=request.app.state.settings.session_cookie_secure,
        samesite="lax",
        max_age=8 * 60 * 60,
    )
    return TokenResponse(access_token=token)


@router.get("/overview")
async def admin_overview(
    staff: StaffUser = Depends(
        require_staff(
            StaffRole.SUPERADMIN,
            StaffRole.ENGINEERING,
            StaffRole.SUPPORT,
            StaffRole.FACTORY,
        )
    ),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    users = await session.scalar(select(func.count()).select_from(User))
    devices = await session.scalar(select(func.count()).select_from(Device))
    owned = await session.scalar(
        select(func.count()).select_from(Device).where(Device.owner_user_id.is_not(None))
    )
    return {
        "role": staff.role,
        "users": int(users or 0),
        "devices": int(devices or 0),
        "owned_devices": int(owned or 0),
    }


def _admin_device_response(device: Device) -> AdminDeviceResponse:
    return AdminDeviceResponse(
        id=device.id,
        serial_number=device.serial_number,
        board_type=device.board_type,
        lifecycle=device.lifecycle,
        owner_user_id=device.owner_user_id,
        active_agent_id=device.active_agent_id,
        hardware_version=device.hardware_version,
        firmware_version=device.firmware_version,
        last_seen_at=device.last_seen_at,
    )


@router.get("/devices", response_model=list[AdminDeviceResponse])
async def admin_devices(
    query: str = "",
    staff: StaffUser = Depends(
        require_staff(
            StaffRole.SUPERADMIN,
            StaffRole.ENGINEERING,
            StaffRole.SUPPORT,
            StaffRole.FACTORY,
        )
    ),
    session: AsyncSession = Depends(get_session),
) -> list[AdminDeviceResponse]:
    statement = select(Device).order_by(Device.created_at.desc()).limit(200)
    if query:
        statement = statement.where(
            or_(
                Device.serial_number.contains(query),
                Device.name.contains(query),
            )
        )
    if staff.role == StaffRole.FACTORY.value:
        statement = statement.where(Device.lifecycle == DeviceLifecycle.FACTORY_UNCLAIMED.value)
    devices = list(await session.scalars(statement))
    return [_admin_device_response(device) for device in devices]


@router.post("/devices/batches", response_model=list[DeviceRegistrationResponse])
async def register_device_batch(
    payload: DeviceBatchRegistrationRequest,
    request: Request,
    staff: StaffUser = Depends(require_staff(StaffRole.SUPERADMIN, StaffRole.FACTORY)),
    session: AsyncSession = Depends(get_session),
) -> list[DeviceRegistrationResponse]:
    serials = [item.serial_number for item in payload.devices]
    existing = list(
        await session.scalars(select(Device.serial_number).where(Device.serial_number.in_(serials)))
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"existing_serials": sorted(existing)},
        )
    responses: list[DeviceRegistrationResponse] = []
    for item in payload.devices:
        device_secret = new_device_secret()
        device = Device(
            serial_number=item.serial_number,
            board_type=item.board_type,
            credential_hash=hash_secret(
                device_secret,
                request.app.state.settings.device_credential_pepper,
            ),
        )
        session.add(device)
        await session.flush()
        responses.append(
            DeviceRegistrationResponse(
                device_id=device.id,
                serial_number=device.serial_number,
                device_secret=device_secret,
                lifecycle=device.lifecycle,
            )
        )
    add_audit_event(
        session,
        actor_type="staff",
        actor_id=staff.id,
        action="device.batch-registered",
        payload={"count": len(responses), "device_ids": [item.device_id for item in responses]},
    )
    await session.commit()
    return responses


@router.post("/devices/{device_id}/rma", response_model=AdminDeviceResponse)
async def quarantine_device_for_rma(
    device_id: str,
    payload: DeviceRmaRequest,
    staff: StaffUser = Depends(require_staff(StaffRole.SUPERADMIN, StaffRole.SUPPORT)),
    session: AsyncSession = Depends(get_session),
) -> AdminDeviceResponse:
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    device.lifecycle = DeviceLifecycle.RMA_QUARANTINE.value
    device.owner_user_id = None
    device.active_agent_id = None
    device.reset_epoch += 1
    add_audit_event(
        session,
        actor_type="staff",
        actor_id=staff.id,
        action="device.rma-quarantined",
        payload={"device_id": device.id, "reason": payload.reason},
    )
    await session.commit()
    return _admin_device_response(device)


@router.get("/users", response_model=list[AdminUserResponse])
async def admin_users(
    query: str = "",
    _: StaffUser = Depends(require_staff(StaffRole.SUPERADMIN, StaffRole.SUPPORT)),
    session: AsyncSession = Depends(get_session),
) -> list[AdminUserResponse]:
    statement = select(User).order_by(User.created_at.desc()).limit(100)
    if query:
        statement = statement.where(User.display_name.contains(query))
    users = list(await session.scalars(statement))
    responses: list[AdminUserResponse] = []
    for user in users:
        device_count = await session.scalar(
            select(func.count()).select_from(Device).where(Device.owner_user_id == user.id)
        )
        responses.append(
            AdminUserResponse(
                id=user.id,
                display_name=user.display_name,
                adult_confirmed=user.adult_confirmed,
                device_count=int(device_count or 0),
                created_at=user.created_at,
            )
        )
    return responses


@router.get("/audit", response_model=list[AdminAuditResponse])
async def admin_audit(
    _: StaffUser = Depends(
        require_staff(StaffRole.SUPERADMIN, StaffRole.ENGINEERING, StaffRole.SUPPORT)
    ),
    session: AsyncSession = Depends(get_session),
) -> list[AdminAuditResponse]:
    events = list(
        await session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(200))
    )
    return [
        AdminAuditResponse(
            id=event.id,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            action=event.action,
            payload=json.loads(event.payload_json or "{}"),
            created_at=event.created_at,
        )
        for event in events
    ]


@router.get("/provider-usage")
async def admin_provider_usage(
    _: StaffUser = Depends(require_staff(StaffRole.SUPERADMIN, StaffRole.ENGINEERING)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    rows = (
        await session.execute(
            select(
                ProviderUsage.provider,
                ProviderUsage.model,
                ProviderUsage.operation,
                func.count(ProviderUsage.id),
                func.sum(ProviderUsage.cost_micros),
                func.sum(ProviderUsage.input_units),
                func.sum(ProviderUsage.output_units),
            ).group_by(
                ProviderUsage.provider,
                ProviderUsage.model,
                ProviderUsage.operation,
            )
        )
    ).all()
    return [
        {
            "provider": provider,
            "model": model,
            "operation": operation,
            "requests": int(requests or 0),
            "cost_micros": int(cost or 0),
            "input_units": int(input_units or 0),
            "output_units": int(output_units or 0),
        }
        for provider, model, operation, requests, cost, input_units, output_units in rows
    ]
