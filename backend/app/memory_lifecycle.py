"""Database fences shared by HTTP memory edits and background summary writers."""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Agent


async def invalidate_memory(
    session: AsyncSession, user_id: str, agent_id: str | None = None,
) -> None:
    statement = update(Agent).where(Agent.owner_user_id == user_id)
    if agent_id is not None:
        statement = statement.where(Agent.id == agent_id)
    # UPDATE serializes with the gateway's conditional UPDATE until commit.
    await session.execute(statement.values(memory_epoch=Agent.memory_epoch + 1))
