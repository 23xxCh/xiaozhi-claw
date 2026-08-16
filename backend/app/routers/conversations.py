from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..dependencies import require_adult_user
from ..models import Agent, ConversationSession, EncryptedSessionSummary, User
from ..schemas import ConversationResponse
from ..security import decrypt_memory

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> list[ConversationResponse]:
    conversations = list(
        await session.scalars(
            select(ConversationSession)
            .where(ConversationSession.user_id == user.id)
            .order_by(ConversationSession.started_at.desc())
            .limit(100)
        )
    )
    consented_agents = set(
        await session.scalars(
            select(Agent.id).where(
                Agent.owner_user_id == user.id,
                Agent.memory_consent.is_(True),
            )
        )
    )
    summaries = {
        item.session_id: item
        for item in list(
            await session.scalars(
                select(EncryptedSessionSummary).where(
                    EncryptedSessionSummary.user_id == user.id,
                    EncryptedSessionSummary.agent_id.in_(consented_agents),
                )
            )
        )
    }
    return [
        ConversationResponse(
            id=item.id,
            agent_id=item.agent_id,
            device_id=item.device_id,
            turn_count=item.turn_count,
            first_audio_latency_ms=item.first_audio_latency_ms,
            provider_cost_micros=item.provider_cost_micros,
            end_reason=item.end_reason,
            started_at=item.started_at,
            ended_at=item.ended_at,
            summary=(
                decrypt_memory(summaries[item.id].encrypted_summary, request.app.state.settings)
                if item.id in summaries
                else None
            ),
        )
        for item in conversations
    ]
