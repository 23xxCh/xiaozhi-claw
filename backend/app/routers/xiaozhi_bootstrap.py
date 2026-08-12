from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Body, Header, HTTPException, Request, status
from sqlalchemy import select

from ..audit import add_audit_event
from ..models import Claim, Device, DeviceLifecycle
from ..security import (
    create_device_session_token,
    hash_secret,
    new_claim_code,
    verify_secret,
)
from .ota import select_release

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
    authorization: str = Header(default=""),
    system_info: dict[str, object] = Body(default_factory=dict),
) -> dict[str, object]:
    """Translate the upstream XiaoZhi bootstrap shape for local pilot devices."""
    settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        device = await session.scalar(select(Device).where(Device.serial_number == device_id))
        if device is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="device not registered"
            )
        secret = (
            authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        )
        if settings.app_env == "production" and not verify_secret(
            secret,
            device.credential_hash,
            settings.device_credential_pepper,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid device credential",
            )

        now = datetime.now(UTC)
        version = _firmware_version(system_info)
        if version:
            device.firmware_version = version
        device.last_seen_at = now
        if device.lifecycle != DeviceLifecycle.OWNED.value or not device.owner_user_id:
            if device.lifecycle != DeviceLifecycle.FACTORY_UNCLAIMED.value:
                raise HTTPException(
                    status_code=status.HTTP_423_LOCKED,
                    detail=device.lifecycle,
                )
            old_claims = list(
                await session.scalars(
                    select(Claim).where(
                        Claim.device_id == device.id,
                        Claim.consumed_at.is_(None),
                    )
                )
            )
            for old_claim in old_claims:
                old_claim.consumed_at = now
            code = new_claim_code()
            claim = Claim(
                code_hash=hash_secret(code, settings.device_credential_pepper),
                device_id=device.id,
                expires_at=now + timedelta(seconds=settings.claim_ttl_seconds),
            )
            session.add(claim)
            add_audit_event(
                session,
                actor_type="device",
                actor_id=device.id,
                action="claim.code-issued",
                payload={"claim_id": claim.id},
            )
            await session.commit()
            return {
                "activation": {
                    "code": code,
                    "message": "请在 Hensun AI 网页输入 6 位绑定码",
                    "timeout_ms": settings.claim_ttl_seconds * 1000,
                },
                "server_time": {
                    "timestamp": int(now.timestamp() * 1000),
                    "timezone_offset": 0,
                },
            }

        add_audit_event(
            session,
            actor_type="device",
            actor_id=device.id,
            action="device.xiaozhi-bootstrap",
            payload={"serial_number": device.serial_number, "firmware_version": version},
        )
        release = await select_release(
            session,
            board_type=device.board_type,
            serial_number=device.serial_number,
            current_version=version or device.firmware_version,
        )
        await session.commit()

    response: dict[str, object] = {
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
    if release is not None:
        response["firmware"] = {
            "version": release.version,
            "url": release.artifact_url,
            "sha256": release.sha256,
            "signature": release.signature,
            "force": 1 if release.mandatory else 0,
        }
    return response
