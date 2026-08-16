import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import require_user
from ..email_delivery import deliver_login_code
from ..models import EmailLoginChallenge, User
from ..schemas import (
    AdultConfirmationRequest,
    DevLoginRequest,
    EmailCodeRequest,
    EmailCodeRequestResponse,
    EmailCodeVerifyRequest,
    EmailLoginResponse,
    TokenResponse,
    UserResponse,
    WechatLoginStartResponse,
)
from ..security import (
    create_access_token,
    create_oauth_state,
    hash_email_code,
    hash_secret,
    new_email_code,
    verify_oauth_state,
)
from ..usage_profiles import ensure_adult_profile

router = APIRouter(prefix="/v1/auth", tags=["auth"])
TERMS_VERSION = "2026-08-13"
PRIVACY_VERSION = "2026-08-13"


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        adult_confirmed=user.adult_confirmed,
        agreements_complete=bool(user.terms_accepted_at and user.ai_disclosure_confirmed_at),
    )


@router.post(
    "/email/request-code",
    response_model=EmailCodeRequestResponse,
    response_model_exclude_none=True,
)
async def request_email_code(
    payload: EmailCodeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> EmailCodeRequestResponse:
    settings = request.app.state.settings
    email = str(payload.email).lower()
    now = datetime.now(UTC)
    await session.execute(
        delete(EmailLoginChallenge).where(
            EmailLoginChallenge.created_at < now - timedelta(days=1)
        )
    )
    recent_after = now - timedelta(seconds=settings.email_otp_resend_seconds)
    recent = await session.scalar(
        select(EmailLoginChallenge.id).where(
            EmailLoginChallenge.email == email,
            EmailLoginChallenge.created_at >= recent_after,
        )
    )
    if recent is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="email code rate limit",
        )

    remote_host = request.client.host if request.client else "unknown"
    ip_hash = hash_secret(f"email-login-ip:{remote_host}", settings.email_otp_secret)
    ip_window = now - timedelta(minutes=10)
    ip_requests = await session.scalar(
        select(func.count())
        .select_from(EmailLoginChallenge)
        .where(
            EmailLoginChallenge.request_ip_hash == ip_hash,
            EmailLoginChallenge.created_at >= ip_window,
        )
    )
    if int(ip_requests or 0) >= settings.email_ip_request_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="email code rate limit",
        )

    code = new_email_code()
    challenge = EmailLoginChallenge(
        email=email,
        code_hash=hash_email_code(email, code, settings),
        request_ip_hash=ip_hash,
        expires_at=now + timedelta(seconds=settings.email_otp_ttl_seconds),
    )
    session.add(challenge)
    await session.commit()
    try:
        await deliver_login_code(settings, email, code)
    except Exception as exc:
        challenge.consumed_at = datetime.now(UTC)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="email delivery unavailable",
        ) from exc
    return EmailCodeRequestResponse(
        expires_in=settings.email_otp_ttl_seconds,
        resend_after=settings.email_otp_resend_seconds,
        debug_code=(code if settings.email_delivery_mode == "development" else None),
    )


@router.post("/email/verify-code", response_model=EmailLoginResponse)
async def verify_email_code(
    payload: EmailCodeVerifyRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> EmailLoginResponse:
    settings = request.app.state.settings
    email = str(payload.email).lower()
    challenge = await session.scalar(
        select(EmailLoginChallenge)
        .where(EmailLoginChallenge.email == email)
        .order_by(EmailLoginChallenge.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    now = datetime.now(UTC)
    expires_at = challenge.expires_at if challenge is not None else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        challenge is None
        or challenge.consumed_at is not None
        or expires_at is None
        or expires_at <= now
        or challenge.attempts >= settings.email_otp_max_attempts
    ):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="email code expired")

    expected_hash = hash_email_code(email, payload.code, settings)
    if not secrets.compare_digest(expected_hash, challenge.code_hash):
        challenge.attempts += 1
        if challenge.attempts >= settings.email_otp_max_attempts:
            challenge.consumed_at = now
        await session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid email code")

    challenge.consumed_at = now
    user = await session.scalar(select(User).where(User.email == email))
    if user is None and settings.app_env != "production":
        legacy_users = list(
            await session.scalars(select(User).where(User.email.is_(None)).limit(2))
        )
        if len(legacy_users) == 1:
            user = legacy_users[0]
    if user is None:
        user = User(
            email=email,
            email_verified_at=now,
            display_name=email.split("@", 1)[0][:80],
        )
        session.add(user)
        await session.flush()
        add_audit_event(
            session,
            actor_type="user",
            actor_id=user.id,
            action="auth.email-user-created",
        )
    else:
        user.email = email
        user.email_verified_at = now
        if user.display_name in {"微信用户", "Hensun 用户"}:
            user.display_name = email.split("@", 1)[0][:80]
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="auth.email-login",
    )
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
    return EmailLoginResponse(
        access_token=token,
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
    await ensure_adult_profile(session, user)
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
    await ensure_adult_profile(session, user)
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
