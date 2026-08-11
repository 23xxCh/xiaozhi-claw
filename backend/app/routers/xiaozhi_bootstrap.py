from datetime import UTC, datetime

from fastapi import APIRouter, Body, Header, HTTPException, Request, status
from sqlalchemy import select

from ..audit import add_audit_event
from ..models import Device, DeviceLifecycle
from ..security import create_device_session_token

router = APIRouter(tags=["xiaozhi-compatibility"])


def _firmware_version(system_info: dict[str, object]) -> str | None:
    application = system_info.get("application")
    if not isinstance(application, dict):
        return None
    version = application.get("version")
    return version[:32] if isinstance(version, str) and version else None


@router.post("/v1/device/xiaozhi-bootstrap")
async def xiaozhi_bootstrap(
    request: Request,
    device_id: str = Header(alias="Device-Id", min_length=6, max_length=64),
    system_info: dict[str, object] = Body(default_factory=dict),
) -> dict[str, object]:
    """Translate the upstream XiaoZhi bootstrap shape for local pilot devices."""
    settings = request.app.state.settings
    if settings.app_env == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    async with request.app.state.session_factory() as session:
        device = await session.scalar(select(Device).where(Device.serial_number == device_id))
        if device is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="device not registered"
            )
        if device.lifecycle != DeviceLifecycle.OWNED.value or not device.owner_user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="device not claimed")

        now = datetime.now(UTC)
        version = _firmware_version(system_info)
        if version:
            device.firmware_version = version
        device.last_seen_at = now
        add_audit_event(
            session,
            actor_type="device",
            actor_id=device.id,
            action="device.xiaozhi-bootstrap",
            payload={"serial_number": device.serial_number, "firmware_version": version},
        )
        await session.commit()

    return {
        "websocket": {
            "url": settings.device_ws_url,
            "token": create_device_session_token(device_id, settings),
            "version": 1,
        },
        "server_time": {
            "timestamp": int(now.timestamp() * 1000),
            "timezone_offset": 0,
        },
    }
