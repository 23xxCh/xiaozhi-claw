from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import require_adult_user
from ..models import (
    Agent,
    AgentMemory,
    ConversationSession,
    EncryptedSessionSummary,
    MemorySummary,
    User,
)
from ..schemas import MemoryExportResponse, SessionSummaryUpdateRequest
from ..security import decrypt_memory, encrypt_memory

router = APIRouter(prefix="/v1", tags=["memory-portability"])


@router.get("/memories/export", response_model=MemoryExportResponse)
async def export_memories(
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> MemoryExportResponse:
    memories = list(
        await session.scalars(select(AgentMemory).where(AgentMemory.user_id == user.id))
    )
    summaries = list(
        await session.scalars(
            select(EncryptedSessionSummary).where(EncryptedSessionSummary.user_id == user.id)
        )
    )
    return MemoryExportResponse(
        generated_at=datetime.now(UTC),
        agent_memories=[
            {
                "agent_id": item.agent_id,
                "key": item.key,
                "value": decrypt_memory(item.encrypted_value, request.app.state.settings),
            }
            for item in memories
        ],
        session_summaries=[
            {
                "agent_id": item.agent_id,
                "session_id": item.session_id,
                "summary": decrypt_memory(
                    item.encrypted_summary,
                    request.app.state.settings,
                ),
            }
            for item in summaries
        ],
    )


@router.delete("/memories", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_account_memories(
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await session.execute(delete(MemorySummary).where(MemorySummary.user_id == user.id))
    await session.execute(delete(AgentMemory).where(AgentMemory.user_id == user.id))
    await session.execute(
        delete(EncryptedSessionSummary).where(EncryptedSessionSummary.user_id == user.id)
    )
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="memory.account-deleted",
    )
    await session.commit()


async def _owned_summary(
    session: AsyncSession,
    user: User,
    conversation_id: str,
) -> tuple[ConversationSession, EncryptedSessionSummary]:
    conversation = await session.get(ConversationSession, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    summary = await session.scalar(
        select(EncryptedSessionSummary).where(EncryptedSessionSummary.session_id == conversation.id)
    )
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="summary not found")
    return conversation, summary


@router.put("/conversations/{conversation_id}/summary")
async def update_session_summary(
    conversation_id: str,
    payload: SessionSummaryUpdateRequest,
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    conversation, summary = await _owned_summary(session, user, conversation_id)
    agent = await session.get(Agent, conversation.agent_id)
    if agent is None or not agent.memory_consent:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="memory is disabled")
    summary.encrypted_summary = encrypt_memory(payload.summary, request.app.state.settings)
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="memory.session-summary-updated",
        payload={"conversation_id": conversation.id},
    )
    await session.commit()
    return {"session_id": conversation.id, "summary": payload.summary}


@router.delete(
    "/conversations/{conversation_id}/summary",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_session_summary(
    conversation_id: str,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    conversation, summary = await _owned_summary(session, user, conversation_id)
    await session.delete(summary)
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="memory.session-summary-deleted",
        payload={"conversation_id": conversation.id},
    )
    await session.commit()
