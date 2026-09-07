from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..catalog import ensure_default_agent
from ..db import get_session
from ..dependencies import require_adult_user
from ..models import Agent, ConversationSession, Device, DeviceSession, User
from ..schemas import OnboardingStatusResponse

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])


@router.get("/status", response_model=OnboardingStatusResponse)
async def onboarding_status(
    request: Request,
    device_id: str | None = None,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> OnboardingStatusResponse:
    query = select(Device).where(Device.owner_user_id == user.id)
    if device_id is not None:
        query = query.where(Device.id == device_id)
    device = await session.scalar(query.order_by(Device.created_at).limit(1))
    if device_id is not None and device is None:
        raise HTTPException(status_code=404, detail="device not found")
    if device is None:
        return OnboardingStatusResponse(
            device_bound=False,
            assistant_configured=False,
            device_online=False,
            first_conversation_complete=False,
            next_action="bind_device",
            active_device_id=None,
            active_agent_id=None,
        )

    agent = await session.get(Agent, device.active_agent_id) if device.active_agent_id else None
    if agent is None or agent.owner_user_id != user.id:
        agent = await ensure_default_agent(session, user)
        await session.commit()
    configured = agent.config_version > 1 or agent.name != "我的助手"
    cutoff = datetime.now(UTC) - timedelta(
        seconds=request.app.state.settings.device_offline_after_seconds
    )
    latest_session = await session.scalar(
        select(DeviceSession)
        .where(DeviceSession.device_id == device.id)
        .order_by(DeviceSession.connected_at.desc(), DeviceSession.id.desc())
        .limit(1)
    )
    heartbeat = latest_session.heartbeat_at if latest_session else None
    if heartbeat is not None and heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    online = bool(
        latest_session and latest_session.status == "online" and heartbeat and heartbeat >= cutoff
    )
    first_conversation = await session.scalar(
        select(ConversationSession.id).where(
            ConversationSession.user_id == user.id,
            ConversationSession.device_id == device.id,
            ConversationSession.turn_count > 0,
        )
    )
    completed = first_conversation is not None
    if not online:
        next_action = "bring_device_online"
    elif not completed:
        next_action = "start_conversation"
    else:
        next_action = "complete"
    return OnboardingStatusResponse(
        device_bound=True,
        assistant_configured=configured,
        device_online=online,
        first_conversation_complete=completed,
        next_action=next_action,
        active_device_id=device.id,
        active_agent_id=device.active_agent_id or agent.id,
    )
