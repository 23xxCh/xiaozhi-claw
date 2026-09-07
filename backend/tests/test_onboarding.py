import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import Agent, ConversationSession, Device, DeviceSession, User

from .conftest import provision_owned_device


def test_onboarding_reports_one_real_next_action_at_each_stage(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    login = client.post(
        "/v1/auth/dev-login",
        json={"openid": "wx-onboarding", "adult_confirmed": True},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    empty = client.get("/v1/onboarding/status", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["next_action"] == "bind_device"

    owned = provision_owned_device(
        client,
        admin_headers,
        serial="HENSUN-ONBOARD-01",
        openid="wx-onboarding",
    )
    headers = {"Authorization": f"Bearer {owned['user_token']}"}
    configured = client.get("/v1/onboarding/status", headers=headers)
    assert configured.json()["assistant_configured"] is False
    assert configured.json()["next_action"] == "bring_device_online"
    agent_id = configured.json()["active_agent_id"]

    updated = client.patch(f"/v1/agents/{agent_id}", headers=headers, json={"name": "小恒"})
    assert updated.status_code == 200
    offline = client.get("/v1/onboarding/status", headers=headers)
    assert offline.json()["next_action"] == "bring_device_online"

    async def seed_runtime() -> None:
        async with client.app.state.session_factory() as session:
            device = await session.get(Device, owned["device_id"])
            assert device is not None and device.active_profile_id and device.active_agent_id
            now = datetime.now(UTC)
            session.add(
                DeviceSession(
                    device_id=device.id,
                    gateway_id="test-gateway",
                    connection_id="test-onboarding-online",
                    connected_at=now,
                    heartbeat_at=now,
                )
            )
            await session.commit()

    asyncio.run(seed_runtime())
    ready = client.get("/v1/onboarding/status", headers=headers)
    assert ready.json()["next_action"] == "start_conversation"

    async def seed_conversation() -> None:
        async with client.app.state.session_factory() as session:
            device = await session.get(Device, owned["device_id"])
            assert device is not None and device.active_profile_id and device.active_agent_id
            user = await session.scalar(select(User).where(User.wechat_openid == "wx-onboarding"))
            assert user is not None
            session.add(
                ConversationSession(
                    user_id=user.id,
                    device_id=device.id,
                    agent_id=device.active_agent_id,
                    usage_profile_id=device.active_profile_id,
                    turn_count=1,
                )
            )
            await session.commit()

    asyncio.run(seed_conversation())
    complete = client.get("/v1/onboarding/status", headers=headers)
    assert complete.json()["next_action"] == "complete"
    assert complete.json()["first_conversation_complete"] is True


def test_onboarding_tracks_the_selected_owned_device_not_account_history(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    first = provision_owned_device(
        client, admin_headers, serial="ONBOARD-FIRST", openid="same-owner"
    )
    second = provision_owned_device(
        client, admin_headers, serial="ONBOARD-NEW", openid="same-owner"
    )
    foreign = provision_owned_device(
        client, admin_headers, serial="ONBOARD-OTHER", openid="other-owner"
    )
    headers = {"Authorization": f"Bearer {second['user_token']}"}

    async def seed_first_device_history() -> str:
        async with client.app.state.session_factory() as session:
            a = await session.get(Device, first["device_id"])
            b = await session.get(Device, second["device_id"])
            assert a and b
            agent = Agent(
                owner_user_id=b.owner_user_id, usage_profile_id=b.active_profile_id,
                name="新设备专用助手", system_prompt="正常回答", model_preset_id="fast-chat",
                voice_preset_id="cherry",
            )
            session.add(agent)
            await session.flush()
            b.active_agent_id = agent.id
            now = datetime.now(UTC)
            session.add_all([
                DeviceSession(
                    id="00000000-0000-0000-0000-000000000002",
                    device_id=b.id, gateway_id="test", connection_id="second-online",
                    status="online", connected_at=now, heartbeat_at=now,
                ),
                DeviceSession(
                    id="00000000-0000-0000-0000-000000000001",
                    device_id=b.id, gateway_id="test", connection_id="second-offline",
                    status="offline", connected_at=now, heartbeat_at=now,
                ),
            ])
            session.add(ConversationSession(
                user_id=a.owner_user_id, device_id=a.id, agent_id=a.active_agent_id,
                usage_profile_id=a.active_profile_id, turn_count=1,
            ))
            await session.commit()
            return agent.id

    second_agent_id = asyncio.run(seed_first_device_history())
    result = client.get(
        "/v1/onboarding/status", params={"device_id": second["device_id"]}, headers=headers
    )
    assert result.status_code == 200
    assert result.json() == {
        "device_bound": True, "assistant_configured": True, "device_online": True,
        "first_conversation_complete": False, "next_action": "start_conversation",
        "active_device_id": second["device_id"], "active_agent_id": second_agent_id,
    }
    devices = client.get("/v1/devices", headers=headers)
    assert devices.status_code == 200
    selected_device = next(item for item in devices.json() if item["id"] == second["device_id"])
    assert selected_device["online"] is result.json()["device_online"] is True
    original = client.get("/v1/onboarding/status", headers=headers).json()
    assert original["active_device_id"] == first["device_id"]
    assert original["first_conversation_complete"] is True
    for device_id in (foreign["device_id"], "does-not-exist"):
        denied = client.get(
            "/v1/onboarding/status", params={"device_id": device_id}, headers=headers
        )
        assert denied.status_code == 404
        assert denied.json()["code"] == "NOT_FOUND"
