from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import require_adult_user
from ..models import Device, MemorySummary, User
from ..schemas import MemoryResponse, MemoryUpsertRequest
from ..security import decrypt_memory, encrypt_memory

router = APIRouter(prefix="/v1/devices/{device_id}/memories", tags=["memories"])


async def _owned_device(session: AsyncSession, user: User, device_id: str) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    return device


def _response(memory: MemorySummary, request: Request) -> MemoryResponse:
    return MemoryResponse(
        id=memory.id,
        key=memory.key,
        value=decrypt_memory(memory.encrypted_value, request.app.state.settings),
        updated_at=memory.updated_at,
    )


@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    device_id: str,
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> list[MemoryResponse]:
    await _owned_device(session, user, device_id)
    memories = list(
        await session.scalars(
            select(MemorySummary)
            .where(MemorySummary.device_id == device_id, MemorySummary.user_id == user.id)
            .order_by(MemorySummary.key)
        )
    )
    return [_response(memory, request) for memory in memories]


@router.put("/{key}", response_model=MemoryResponse)
async def upsert_memory(
    device_id: str,
    key: str,
    payload: MemoryUpsertRequest,
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> MemoryResponse:
    if key != payload.key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="memory key mismatch")
    device = await _owned_device(session, user, device_id)
    if not device.memory_consent:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="memory consent is disabled"
        )
    memory = await session.scalar(
        select(MemorySummary).where(
            MemorySummary.device_id == device_id, MemorySummary.key == payload.key
        )
    )
    encrypted = encrypt_memory(payload.value, request.app.state.settings)
    if memory is None:
        memory = MemorySummary(
            user_id=user.id,
            device_id=device_id,
            key=payload.key,
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
        action="memory.upserted",
        payload={"device_id": device_id, "key": payload.key},
    )
    await session.commit()
    return _response(memory, request)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    device_id: str,
    key: str,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _owned_device(session, user, device_id)
    result = await session.execute(
        delete(MemorySummary).where(
            MemorySummary.device_id == device_id,
            MemorySummary.user_id == user.id,
            MemorySummary.key == key,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="memory.deleted",
        payload={"device_id": device_id, "key": key},
    )
    await session.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_memories(
    device_id: str,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _owned_device(session, user, device_id)
    await session.execute(
        delete(MemorySummary).where(
            MemorySummary.device_id == device_id, MemorySummary.user_id == user.id
        )
    )
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="memory.all-deleted",
        payload={"device_id": device_id},
    )
    await session.commit()
