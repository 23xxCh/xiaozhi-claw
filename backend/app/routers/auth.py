from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..models import User
from ..schemas import DevLoginRequest, TokenResponse
from ..security import create_access_token

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/dev-login", response_model=TokenResponse)
async def dev_login(
    payload: DevLoginRequest,
    request: Request,
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
        user = User(wechat_openid=payload.openid, adult_confirmed=True)
        session.add(user)
        await session.flush()
        add_audit_event(
            session,
            actor_type="user",
            actor_id=user.id,
            action="auth.dev-user-created",
        )
    else:
        user.adult_confirmed = True
    await session.commit()
    return TokenResponse(access_token=create_access_token(user.id, settings))
