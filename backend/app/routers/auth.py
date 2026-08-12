from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import require_user
from ..models import User
from ..schemas import (
    AdultConfirmationRequest,
    DevLoginRequest,
    TokenResponse,
    UserResponse,
    WechatLoginStartResponse,
)
from ..security import create_access_token, create_oauth_state, verify_oauth_state

router = APIRouter(prefix="/v1/auth", tags=["auth"])
TERMS_VERSION = "2026-08-13"
PRIVACY_VERSION = "2026-08-13"


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        display_name=user.display_name,
        adult_confirmed=user.adult_confirmed,
        agreements_complete=bool(user.terms_accepted_at and user.ai_disclosure_confirmed_at),
    )


@router.post("/dev-login", response_model=TokenResponse)
async def dev_login(
    payload: DevLoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    settings = request.app.state.settings
    if settings.app_env == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if not payload.adult_confirmed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="18+ confirmation required"
        )

    user = await session.scalar(select(User).where(User.wechat_openid == payload.openid))
    if user is None:
        now = datetime.now(UTC)
        user = User(
            wechat_openid=payload.openid,
            adult_confirmed=True,
            terms_version=TERMS_VERSION,
            privacy_version=PRIVACY_VERSION,
            terms_accepted_at=now,
            ai_disclosure_confirmed_at=now,
        )
        session.add(user)
        await session.flush()
        add_audit_event(
            session,
            actor_type="user",
            actor_id=user.id,
            action="auth.dev-user-created",
        )
    else:
        now = datetime.now(UTC)
        user.adult_confirmed = True
        user.terms_version = TERMS_VERSION
        user.privacy_version = PRIVACY_VERSION
        user.terms_accepted_at = now
        user.ai_disclosure_confirmed_at = now
    await session.commit()
    token = create_access_token(user.id, settings)
    response.set_cookie(
        "hensun_session",
        token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=12 * 60 * 60,
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(require_user)) -> UserResponse:
    return _user_response(user)


@router.post("/adult-confirmation", response_model=UserResponse)
async def confirm_adult(
    payload: AdultConfirmationRequest,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    if not all(
        [
            payload.confirmed,
            payload.accepted_terms,
            payload.accepted_privacy,
            payload.acknowledged_ai,
        ]
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="18+ confirmation required"
        )
    now = datetime.now(UTC)
    user.adult_confirmed = True
    user.terms_version = TERMS_VERSION
    user.privacy_version = PRIVACY_VERSION
    user.terms_accepted_at = now
    user.ai_disclosure_confirmed_at = now
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="auth.agreements-accepted",
        payload={"terms_version": TERMS_VERSION, "privacy_version": PRIVACY_VERSION},
    )
    await session.commit()
    return _user_response(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie("hensun_session")


@router.get("/wechat/start", response_model=WechatLoginStartResponse)
async def wechat_start(request: Request) -> WechatLoginStartResponse:
    settings = request.app.state.settings
    if not settings.wechat_web_app_id or not settings.wechat_web_redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="wechat login not configured"
        )
    state_token = create_oauth_state(settings)
    query = urlencode(
        {
            "appid": settings.wechat_web_app_id,
            "redirect_uri": settings.wechat_web_redirect_uri,
            "response_type": "code",
            "scope": "snsapi_login",
            "state": state_token,
        }
    )
    return WechatLoginStartResponse(
        authorization_url=f"https://open.weixin.qq.com/connect/qrconnect?{query}#wechat_redirect"
    )


@router.get("/wechat/callback")
async def wechat_callback(
    request: Request,
    code: str,
    state: str,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = request.app.state.settings
    try:
        verify_oauth_state(state, settings)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid oauth state"
        ) from exc
    if not settings.wechat_web_app_id or not settings.wechat_web_app_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="wechat login not configured"
        )
    async with httpx.AsyncClient(timeout=10) as client:
        exchange = await client.get(
            "https://api.weixin.qq.com/sns/oauth2/access_token",
            params={
                "appid": settings.wechat_web_app_id,
                "secret": settings.wechat_web_app_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
    exchange.raise_for_status()
    identity = exchange.json()
    openid = str(identity.get("openid") or "")
    unionid = str(identity.get("unionid") or "") or None
    if not openid:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="wechat identity unavailable"
        )
    user = None
    if unionid:
        user = await session.scalar(select(User).where(User.wechat_unionid == unionid))
    if user is None:
        user = await session.scalar(select(User).where(User.wechat_openid == openid))
    if user is None:
        user = User(wechat_openid=openid, wechat_unionid=unionid, adult_confirmed=False)
        session.add(user)
        await session.flush()
    elif unionid and not user.wechat_unionid:
        user.wechat_unionid = unionid
    await session.commit()
    token = create_access_token(user.id, settings)
    redirect = RedirectResponse(
        f"{settings.web_app_url.rstrip('/')}/auth/complete", status_code=302
    )
    redirect.set_cookie(
        "hensun_session",
        token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=12 * 60 * 60,
    )
    return redirect
