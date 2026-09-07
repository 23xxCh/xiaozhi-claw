import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.models import (
    AgentMemory,
    ConversationSession,
    Device,
    EncryptedSessionSummary,
    MemorySummary,
)
from backend.app.security import encrypt_memory

from .conftest import provision_owned_device


def test_memory_is_opt_in_and_can_be_deleted(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    headers = {"Authorization": f"Bearer {owned['user_token']}"}
    path = f"/v1/devices/{owned['device_id']}/memories/preferred-name"
    payload = {"key": "preferred-name", "value": "用户希望被叫作小林"}

    rejected = client.put(path, headers=headers, json=payload)
    assert rejected.status_code == 409

    consent = client.patch(
        f"/v1/devices/{owned['device_id']}/memory-consent",
        headers=headers,
        json={"enabled": True},
    )
    assert consent.status_code == 200

    created = client.put(path, headers=headers, json=payload)
    assert created.status_code == 200
    assert created.json()["value"] == payload["value"]

    listed = client.get(f"/v1/devices/{owned['device_id']}/memories", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["key"] == "preferred-name"
    assert listed.json()[0]["value"] == payload["value"]

    deleted = client.delete(path, headers=headers)
    assert deleted.status_code == 204
    listed_again = client.get(f"/v1/devices/{owned['device_id']}/memories", headers=headers)
    assert listed_again.json() == []


def test_account_delete_all_memories_removes_legacy_and_new_storage_only_for_owner(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owner = provision_owned_device(client, admin_headers)
    other = provision_owned_device(
        client, admin_headers, serial="HENSUN-MEMORY-OTHER", openid="wx-memory-other"
    )
    headers = {"Authorization": f"Bearer {owner['user_token']}"}

    async def seed_memories() -> list[str]:
        user_ids: list[str] = []
        async with client.app.state.session_factory() as session:
            encrypted = encrypt_memory("本人确认的偏好", client.app.state.settings)
            for owned in (owner, other):
                device = await session.get(Device, owned["device_id"])
                assert device is not None and device.owner_user_id
                user_ids.append(device.owner_user_id)
                session.add(
                    MemorySummary(
                        user_id=device.owner_user_id,
                        device_id=device.id,
                        key="legacy-preference",
                        encrypted_value=encrypted,
                    )
                )
                session.add(
                    AgentMemory(
                        user_id=device.owner_user_id,
                        agent_id=device.active_agent_id,
                        key="agent-preference",
                        encrypted_value=encrypted,
                    )
                )
                conversation = ConversationSession(
                    user_id=device.owner_user_id,
                    device_id=device.id,
                    agent_id=device.active_agent_id,
                    usage_profile_id=device.active_profile_id,
                )
                session.add(conversation)
                await session.flush()
                session.add(
                    EncryptedSessionSummary(
                        user_id=device.owner_user_id,
                        agent_id=device.active_agent_id,
                        session_id=conversation.id,
                        encrypted_summary=encrypted,
                    )
                )
            await session.commit()
        return user_ids

    user_ids = asyncio.run(seed_memories())
    deleted = client.delete("/v1/memories", headers=headers)
    assert deleted.status_code == 204

    async def check_remaining() -> None:
        async with client.app.state.session_factory() as session:
            for model in (MemorySummary, AgentMemory, EncryptedSessionSummary):
                for user_id, expected in zip(user_ids, (0, 1), strict=True):
                    count = await session.scalar(
                        select(func.count()).select_from(model).where(model.user_id == user_id)
                    )
                    assert count == expected, model.__tablename__
            # Deleting personal memory does not delete metering/conversation metadata.
            assert await session.scalar(select(func.count()).select_from(ConversationSession)) == 2

    asyncio.run(check_remaining())
