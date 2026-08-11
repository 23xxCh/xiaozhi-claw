from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..dependencies import require_user
from ..models import User
from ..quota import quota_for_user
from ..schemas import EntitlementResponse

router = APIRouter(prefix="/v1/account", tags=["subscriptions"])


@router.get("/entitlement", response_model=EntitlementResponse)
async def entitlement(
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> EntitlementResponse:
    quota = await quota_for_user(session, user.id, request.app.state.settings)
    return EntitlementResponse(
        plan=quota.plan,
        monthly_turn_limit=quota.limit,
        used_turns=quota.used,
        remaining_turns=quota.remaining,
        expires_at=quota.expires_at,
    )
