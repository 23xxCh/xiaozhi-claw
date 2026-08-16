import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.models import ConversationSession, Device, DeviceSession, DeviceSessionStatus
from backend.app.runtime_state import reconcile_stale_runtime_state

from .conftest import provision_owned_device


def _staff_token(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    username: str = "metrics-engineer",
    role: str = "engineering",
) -> str:
    created = client.post(
        "/v1/admin/staff",
        headers=admin_headers,
        json={"username": username, "display_name": username, "role": role},
    )
    assert created.status_code == 200, created.text
    login = client.post(
        "/v1/admin/auth/login",
        headers=admin_headers,
        json={"username": username},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _complete_mock_turn(websocket) -> None:
    websocket.send_json({"type": "listen", "state": "start"})
    websocket.send_bytes("运营指标测试".encode())
    websocket.send_json({"type": "listen", "state": "stop"})
    assert websocket.receive_json()["type"] == "stt"
    assert websocket.receive_json()["type"] == "llm"
    assert websocket.receive_json()["type"] == "llm"
    assert websocket.receive_json()["state"] == "start"
    assert websocket.receive_json()["state"] == "sentence_start"
    websocket.receive_bytes()
    assert websocket.receive_json()["state"] == "stop"


def test_default_catalog_uses_current_official_list_prices(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    token = _staff_token(client, admin_headers)
    response = client.get(
        "/v1/admin/model-presets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    presets = {item["id"]: item for item in response.json()}

    assert presets["fast-chat"]["asr_cost_micros_per_minute"] == 19_800
    assert presets["fast-chat"]["llm_input_cost_micros_per_million_tokens"] == 1_000_000
    assert presets["fast-chat"]["llm_output_cost_micros_per_million_tokens"] == 2_000_000
    assert presets["fast-chat"]["tts_cost_micros_per_10k_chars"] == 1_000_000
    assert presets["rich-chat"]["llm_input_cost_micros_per_million_tokens"] == 3_000_000
    assert presets["rich-chat"]["llm_output_cost_micros_per_million_tokens"] == 6_000_000


def test_stale_runtime_state_is_reconciled_without_closing_the_active_pair(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-REAPER-01")
    now = datetime.now(UTC)

    async def seed_and_reconcile() -> tuple[object, object]:
        factory = client.app.state.session_factory
        async with factory() as session:
            device_id = owned["device_id"]
            device = await session.get(Device, device_id)
            assert device is not None and device.active_profile_id
            stale_started = now - timedelta(minutes=10)
            active_started = now - timedelta(seconds=20)
            stale_session = DeviceSession(
                device_id=device_id,
                gateway_id="gateway-old",
                connection_id="connection-old",
                connected_at=stale_started,
                heartbeat_at=stale_started,
            )
            active_session = DeviceSession(
                device_id=device_id,
                gateway_id="gateway-current",
                connection_id="connection-current",
                connected_at=active_started,
                heartbeat_at=now,
            )
            old_conversation = ConversationSession(
                user_id="user-old",
                agent_id="agent-old",
                device_id=device_id,
                usage_profile_id=device.active_profile_id,
                started_at=stale_started,
            )
            active_conversation = ConversationSession(
                user_id="user-current",
                agent_id="agent-current",
                device_id=device_id,
                usage_profile_id=device.active_profile_id,
                started_at=active_started,
            )
            session.add_all(
                [stale_session, active_session, old_conversation, active_conversation]
            )
            await session.commit()
            stale_id = stale_session.id
            active_id = active_session.id
            old_conversation_id = old_conversation.id
            active_conversation_id = active_conversation.id

        result = await reconcile_stale_runtime_state(
            factory,
            offline_after_seconds=90,
            now=now,
        )
        async with factory() as session:
            return (
                result,
                {
                    "stale": await session.get(DeviceSession, stale_id),
                    "active": await session.get(DeviceSession, active_id),
                    "old_conversation": await session.get(
                        ConversationSession, old_conversation_id
                    ),
                    "active_conversation": await session.get(
                        ConversationSession, active_conversation_id
                    ),
                },
            )

    result, records = asyncio.run(seed_and_reconcile())
    assert result.sessions_closed == 1
    assert result.conversations_closed == 1
    assert records["stale"].status == DeviceSessionStatus.OFFLINE.value
    assert records["active"].status == DeviceSessionStatus.ONLINE.value
    assert records["old_conversation"].end_reason == "stale-recovered"
    assert records["old_conversation"].ended_at is not None
    assert records["active_conversation"].ended_at is None


def test_admin_metrics_reports_voice_latency_cost_and_provider_health(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-METRICS-01")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        _complete_mock_turn(websocket)

    token = _staff_token(client, admin_headers)
    response = client.get(
        "/v1/admin/metrics",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    metrics = response.json()
    assert metrics["voice_turns_24h"] == 1
    assert metrics["active_users_7d"] == 1
    assert metrics["active_devices_7d"] == 1
    assert metrics["provider_requests_30d"] == 3
    assert metrics["provider_cost_micros_30d"] > 0
    assert metrics["first_audio_p50_ms"] is not None
    assert metrics["first_audio_p95_ms"] is not None
    assert {item["operation"] for item in metrics["provider_latency"]} == {
        "asr",
        "llm",
        "tts",
    }
