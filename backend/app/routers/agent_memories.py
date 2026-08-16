from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import require_adult_user
from ..models import Agent, AgentMemory, User
from ..schemas import AgentMemoryResponse, AgentMemoryUpsertRequest
from ..security import decrypt_memory, encrypt_memory

router = APIRouter(prefix="/v1/agents/{agent_id}/memories", tags=["agent-memories"])


async def _agent(session: AsyncSession, user: User, agent_id: str) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    return agent


def _response(memory: AgentMemory, request: Request) -> AgentMemoryResponse:
    return AgentMemoryResponse(
        id=memory.id,
        key=memory.key,
        value=decrypt_memory(memory.encrypted_value, request.app.state.settings),
        updated_at=memory.updated_at,
    )


@router.get("", response_model=list[AgentMemoryResponse])
async def list_agent_memories(
    agent_id: str,
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> list[AgentMemoryResponse]:
    await _agent(session, user, agent_id)
    memories = list(
        await session.scalars(
            select(AgentMemory).where(AgentMemory.agent_id == agent_id).order_by(AgentMemory.key)
        )
    )
    return [_response(item, request) for item in memories]


@router.put("/{key}", response_model=AgentMemoryResponse)
async def upsert_agent_memory(
    agent_id: str,
    key: str,
    payload: AgentMemoryUpsertRequest,
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> AgentMemoryResponse:
    if key != payload.key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="memory key mismatch")
    agent = await _agent(session, user, agent_id)
    if not agent.memory_consent:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="memory consent is disabled"
        )
    memory = await session.scalar(
        select(AgentMemory).where(AgentMemory.agent_id == agent.id, AgentMemory.key == key)
    )
    encrypted = encrypt_memory(payload.value, request.app.state.settings)
    if memory is None:
        memory = AgentMemory(
            user_id=user.id,
            agent_id=agent.id,
            key=key,
            encrypted_value=encrypted,
        )
        session.add(memory)
    else:
        memory.encrypted_value = encrypted
    await session.flush()
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="agent-memory.upserted",
        payload={"agent_id": agent.id, "key": key},
    )
    await session.commit()
    return _response(memory, request)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_memory(
    agent_id: str,
    key: str,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _agent(session, user, agent_id)
    result = await session.execute(
        delete(AgentMemory).where(AgentMemory.agent_id == agent_id, AgentMemory.key == key)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="agent-memory.deleted",
        payload={"agent_id": agent_id, "key": key},
    )
    await session.commit()
