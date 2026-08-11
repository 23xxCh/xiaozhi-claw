import secrets

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .db import get_session
from .models import Device, User
from .security import decode_access_token, verify_secret


def settings_from_request(request: Request) -> Settings:
    return request.app.state.settings


async def require_admin(
    request: Request,
    x_admin_key: str = Header(default=""),
) -> None:
    settings = settings_from_request(request)
    if not secrets.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin key")


async def require_user(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    try:
        user_id = decode_access_token(
            authorization.removeprefix("Bearer "), request.app.state.settings
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token"
        ) from exc
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown user")
    return user


async def authenticate_device(
    request: Request,
    session: AsyncSession,
    serial_number: str,
    secret: str,
) -> Device:
    device = await session.scalar(select(Device).where(Device.serial_number == serial_number))
    if device is None or not verify_secret(
        secret, device.credential_hash, request.app.state.settings.device_credential_pepper
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid device credential"
        )
    return device
