import secrets

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .db import get_session
from .models import Device, StaffRole, StaffUser, User
from .security import decode_access_token, decode_staff_access_token, verify_secret


def settings_from_request(request: Request) -> Settings:
    return request.app.state.settings


def _validate_cookie_origin(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin", "")
    if origin not in request.app.state.settings.allowed_origins:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid request origin")


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
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    used_cookie = not token
    if used_cookie:
        token = request.cookies.get("hensun_session", "")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    if used_cookie:
        _validate_cookie_origin(request)
    try:
        user_id = decode_access_token(token, request.app.state.settings)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token"
        ) from exc
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown user")
    return user


async def require_adult_user(user: User = Depends(require_user)) -> User:
    if not (user.adult_confirmed and user.terms_accepted_at and user.ai_disclosure_confirmed_at):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="18+ and agreement confirmation required",
        )
    return user


def require_staff(*allowed_roles: StaffRole):
    async def dependency(
        request: Request,
        authorization: str = Header(default=""),
        session: AsyncSession = Depends(get_session),
    ) -> StaffUser:
        token = (
            authorization.removeprefix("Bearer ")
            if authorization.startswith("Bearer ")
            else request.cookies.get("hensun_staff_session", "")
        )
        if not authorization.startswith("Bearer "):
            _validate_cookie_origin(request)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="missing staff token"
            )
        try:
            staff_id, token_role = decode_staff_access_token(token, request.app.state.settings)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid staff token"
            ) from exc
        staff = await session.get(StaffUser, staff_id)
        if staff is None or not staff.active or staff.role != token_role:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown staff")
        if allowed_roles and staff.role not in {role.value for role in allowed_roles}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="role not permitted")
        return staff

    return dependency


def require_admin_or_staff(*allowed_roles: StaffRole):
    async def dependency(
        request: Request,
        authorization: str = Header(default=""),
        x_admin_key: str = Header(default=""),
        session: AsyncSession = Depends(get_session),
    ) -> StaffUser | None:
        settings = request.app.state.settings
        if x_admin_key and secrets.compare_digest(x_admin_key, settings.admin_api_key):
            return None
        token = (
            authorization.removeprefix("Bearer ")
            if authorization.startswith("Bearer ")
            else request.cookies.get("hensun_staff_session", "")
        )
        if not authorization.startswith("Bearer "):
            _validate_cookie_origin(request)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing staff credentials",
            )
        try:
            staff_id, token_role = decode_staff_access_token(token, settings)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid staff token",
            ) from exc
        staff = await session.get(StaffUser, staff_id)
        if staff is None or not staff.active or staff.role != token_role:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown staff")
        if allowed_roles and staff.role not in {role.value for role in allowed_roles}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="role not permitted")
        return staff

    return dependency


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
