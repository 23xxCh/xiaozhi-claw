import asyncio
from types import SimpleNamespace

import pytest
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


@pytest.mark.parametrize("mutation", ["clear", "off-on", "edit", "unbind", "voice"])
def test_delayed_summary_cannot_recreate_invalidated_memory(
    client: TestClient, admin_headers: dict[str, str], mutation: str,
) -> None:
    from backend.realtime.session import _load_snapshot, _save_session_summary

    owned = provision_owned_device(client, admin_headers)
    headers = {"Authorization": f"Bearer {owned['user_token']}"}
    agent_id = client.get("/v1/agents", headers=headers).json()[0]["id"]
    path = f"/v1/agents/{agent_id}"
    assert client.patch(path, headers=headers, json={"memory_consent": True}).status_code == 200

    async def scenario() -> None:
        async with client.app.state.session_factory() as session:
            device = await session.get(Device, owned["device_id"])
            snapshot = await _load_snapshot(session, device)
            user_id = device.owner_user_id
            conversation = ConversationSession(
                user_id=user_id, device_id=device.id, agent_id=agent_id,
                usage_profile_id=device.active_profile_id,
            )
            session.add(conversation)
            await session.commit()
            conversation_id = conversation.id
        started, release = asyncio.Event(), asyncio.Event()

        async def reply_stream(request):
            started.set()
            await release.wait()
            yield "旧的偏好，不应在清除后重新出现"

        client.app.state.realtime_providers = SimpleNamespace(
            llm=SimpleNamespace(reply_stream=reply_stream)
        )
        task = asyncio.create_task(_save_session_summary(
            SimpleNamespace(app=client.app), conversation_id, user_id, snapshot,
            [{"role": "user", "content": "我喜欢旧口味"},
             {"role": "assistant", "content": "知道了"}],
        ))
        try:
            await asyncio.wait_for(started.wait(), timeout=3)
            if mutation == "clear":
                assert client.delete("/v1/memories", headers=headers).status_code == 204
            elif mutation == "off-on":
                for enabled in (False, True):
                    assert client.patch(path, headers=headers, json={
                        "memory_consent": enabled,
                    }).status_code == 200
            elif mutation == "edit":
                assert client.put(f"{path}/memories/preference", headers=headers, json={
                    "key": "preference", "value": "我现在喜欢新口味",
                }).status_code == 200
            elif mutation == "unbind":
                assert client.post(
                    f"/v1/devices/{owned['device_id']}/unbind", headers=headers,
                ).status_code == 200
            else:
                assert client.patch(path, headers=headers, json={
                    "voice_preset_id": "serena", "memory_consent": True,
                }).status_code == 200
        finally:
            release.set()
            await asyncio.wait_for(task, timeout=3)
        async with client.app.state.session_factory() as session:
            assert await session.scalar(
                select(func.count()).select_from(EncryptedSessionSummary)
            ) == (1 if mutation == "voice" else 0)

    asyncio.run(scenario())
