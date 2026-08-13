import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.app.models import UsageProfile
from backend.app.profile_policy import evaluate_profile_policy

from .conftest import provision_owned_device


def _user_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_existing_adult_gets_profile_and_family_mode_stays_closed_by_default(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-PROFILE-01")
    headers = _user_headers(owned["user_token"])

    profiles = client.get("/v1/profiles", headers=headers)
    assert profiles.status_code == 200
    assert len(profiles.json()) == 1
    assert profiles.json()[0]["kind"] == "adult"

    rejected = client.post(
        "/v1/profiles",
        headers=headers,
        json={"display_name": "小禾", "age_band": "14_17", "guardian_confirmed": True},
    )
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "ACCESS_DENIED"
    assert "request_id" in rejected.json()


def test_guardian_can_create_and_switch_to_whitelisted_youth_profile(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    openid = "wx-family-preview"
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-PROFILE-02", openid=openid)
    client.app.state.settings.family_mode_enabled = True
    client.app.state.settings.family_mode_openid_whitelist = openid
    headers = _user_headers(owned["user_token"])

    created = client.post(
        "/v1/profiles",
        headers=headers,
        json={"display_name": "小禾", "age_band": "12_13", "guardian_confirmed": True},
    )
    assert created.status_code == 200, created.text
    youth = created.json()
    assert youth["kind"] == "youth"
    assert youth["memory_consent"] is False
    assert youth["quiet_start"] == "22:00"
    assert youth["quiet_end"] == "07:00"
    assert youth["daily_limit_minutes"] == 90
    assert youth["continuous_reminder_minutes"] == 30

    controls = client.patch(
        f"/v1/profiles/{youth['id']}/guardian-controls",
        headers=headers,
        json={"memory_consent": True, "daily_limit_minutes": 60},
    )
    assert controls.status_code == 200
    assert controls.json()["memory_consent"] is True
    assert controls.json()["daily_limit_minutes"] == 60

    agent = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": "小禾的助手", "usage_profile_id": youth["id"]},
    )
    assert agent.status_code == 200, agent.text
    switched = client.patch(
        f"/v1/devices/{owned['device_id']}/active-profile",
        headers=headers,
        json={"profile_id": youth["id"], "agent_id": agent.json()["id"]},
    )
    assert switched.status_code == 200
    assert switched.json()["active_profile_id"] == youth["id"]
    assert switched.json()["active_agent_id"] == agent.json()["id"]


def test_adult_policy_is_unrestricted_and_youth_quiet_hours_are_enforced(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-PROFILE-03")
    headers = _user_headers(owned["user_token"])
    adult_id = client.get("/v1/profiles", headers=headers).json()[0]["id"]

    async def evaluate() -> tuple[object, object]:
        async with client.app.state.session_factory() as session:
            adult = await session.get(UsageProfile, adult_id)
            assert adult is not None
            youth = UsageProfile(
                owner_user_id=adult.owner_user_id,
                kind="youth",
                display_name="策略测试",
                age_band="14_17",
            )
            session.add(youth)
            await session.flush()
            quiet_time = datetime(2026, 8, 13, 15, 0, tzinfo=UTC)  # 23:00 in Hong Kong
            adult_decision = await evaluate_profile_policy(
                session, adult, family_mode_enabled=True, now=quiet_time
            )
            youth_decision = await evaluate_profile_policy(
                session, youth, family_mode_enabled=True, now=quiet_time
            )
            return adult_decision, youth_decision

    adult_decision, youth_decision = asyncio.run(evaluate())
    assert adult_decision.allowed is True
    assert youth_decision.allowed is False
    assert youth_decision.code == "quiet-hours"
