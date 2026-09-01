import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import ConversationSession, Device, DeviceSession, User

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
