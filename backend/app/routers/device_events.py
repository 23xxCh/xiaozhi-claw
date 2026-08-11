from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import require_admin
from ..models import Device
from ..schemas import DeviceFaceEventRequest, DeviceFaceEventResponse

router = APIRouter(
    prefix="/v1/admin/device-events",
    tags=["device-events"],
    dependencies=[Depends(require_admin)],
)


@router.post("/{serial_number}", response_model=DeviceFaceEventResponse)
async def send_face_event(
    payload: DeviceFaceEventRequest,
    request: Request,
    serial_number: str = Path(pattern=r"^[A-Za-z0-9:-]{6,64}$"),
    session: AsyncSession = Depends(get_session),
) -> DeviceFaceEventResponse:
    device = await session.scalar(select(Device).where(Device.serial_number == serial_number))
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")

    event_message: dict[str, object]
    if payload.message_type == "alert":
        event_message = {
            "type": "alert",
            "status": payload.status or "提示",
            "message": payload.message or "",
            "emotion": payload.event.value,
        }
    else:
        event_message = {"type": "llm", "emotion": payload.event.value}

    delivered = await request.app.state.device_connections.send_json(
        device.serial_number, event_message
    )
    if not delivered:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="device is offline")

    add_audit_event(
        session,
        actor_type="admin",
        actor_id="device-event-api",
        action="device.face-event-sent",
        payload={
            "device_id": device.id,
            "serial_number": device.serial_number,
            "event": payload.event.value,
            "message_type": payload.message_type,
        },
    )
    await session.commit()
    return DeviceFaceEventResponse(
        serial_number=device.serial_number,
        event=payload.event,
        message_type=payload.message_type,
        delivered=True,
    )
