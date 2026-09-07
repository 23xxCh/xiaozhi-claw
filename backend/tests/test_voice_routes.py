import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.models import Device, ProviderUsage
from backend.app.schemas import ProviderUsageDetails

from .conftest import provision_owned_device


def _user_headers(client: TestClient, name: str = "voice-route-owner") -> dict[str, str]:
    response = client.post("/v1/auth/dev-login", json={"openid": name, "adult_confirmed": True})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _staff_headers(client: TestClient, admin_headers: dict[str, str]) -> dict[str, str]:
    response = client.post(
        "/v1/admin/staff",
        headers=admin_headers,
        json={"username": "route-engineer", "display_name": "Route", "role": "engineering"},
    )
    assert response.status_code == 200, response.text
    login = client.post(
        "/v1/admin/auth/login", headers=admin_headers, json={"username": "route-engineer"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _enable_doubao(client: TestClient, staff: dict[str, str], *, default: bool = False) -> None:
    settings = client.app.state.settings
    settings.doubao_realtime_enabled = True
    settings.doubao_api_key = "test-key"
    response = client.patch(
        "/v1/admin/model-presets/doubao-realtime",
        headers=staff,
        json={"enabled": True, "is_default": default, "confirm": True},
    )
    assert response.status_code == 200, response.text


def test_doubao_candidate_is_disabled_and_not_disguised_as_cascade(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    user = _user_headers(client)
    models = client.get("/v1/model-presets", headers=user).json()
    assert {item["id"] for item in models} == {"fast-chat", "rich-chat"}
    assert {item["id"] for item in client.get("/v1/voice-presets", headers=user).json()} == {
        "cherry",
        "ethan",
    }
    staff = _staff_headers(client, admin_headers)
    presets = client.get("/v1/admin/model-presets", headers=staff).json()
    candidate = next(item for item in presets if item["id"] == "doubao-realtime")
    assert candidate["route_kind"] == "realtime_s2s"
    assert candidate["realtime_provider"] == "doubao"
    assert candidate["realtime_model"] == "1.2.6.1"
    assert candidate["enabled"] is candidate["is_default"] is False
    for field in (
        "asr_provider",
        "asr_model",
        "llm_provider",
        "llm_model",
        "tts_provider",
        "tts_model",
    ):
        assert candidate[field] is None
    assert candidate["capabilities"]["llm_temperature"] is False
    assert candidate["capabilities"]["tts_speech_rate"] is False
    assert candidate["capabilities"]["tools"] is True
    assert candidate["default_voice_preset_id"] == "doubao-vv"


@pytest.mark.parametrize("entry", ["create", "agents-list", "claim"])
def test_all_new_agent_entry_points_resolve_current_catalog_default(
    client: TestClient, admin_headers: dict[str, str], entry: str
) -> None:
    staff = _staff_headers(client, admin_headers)
    _enable_doubao(client, staff, default=True)
    if entry == "claim":
        owned = provision_owned_device(client, admin_headers, openid="route-claim-owner")
        user = {"Authorization": f"Bearer {owned['user_token']}"}
        result = client.get("/v1/agents", headers=user).json()[0]
        device = client.get("/v1/devices", headers=user).json()[0]
        assert device["active_agent_id"] == result["id"]
    elif entry == "create":
        response = client.post("/v1/agents", headers=_user_headers(client), json={"name": "新角色"})
        assert response.status_code == 200, response.text
        result = response.json()
    else:
        result = client.get("/v1/agents", headers=_user_headers(client)).json()[0]
    assert result["model_preset_id"] == "doubao-realtime"
    assert result["voice_preset_id"] == "doubao-vv"


def test_switch_is_atomic_and_preserves_cascade_parameters(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    user = _user_headers(client)
    original = client.get("/v1/agents", headers=user).json()[0]
    path = f"/v1/agents/{original['id']}"
    original = client.patch(
        path,
        headers=user,
        json={"llm_temperature": 0.85, "tts_speech_rate": 1.25, "tools": {"calculator": True}},
    ).json()
    original = client.get(path, headers=user).json()
    staff = _staff_headers(client, admin_headers)
    _enable_doubao(client, staff, default=True)
    assert client.get(path, headers=user).json() == original
    rejected = client.patch(path, headers=user, json={"model_preset_id": "doubao-realtime"})
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "VOICE_PRESET_INCOMPATIBLE"
    assert client.get(path, headers=user).json() == original
    switched = client.patch(
        path,
        headers=user,
        json={"model_preset_id": "doubao-realtime", "voice_preset_id": "doubao-vv"},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["config_version"] == original["config_version"] + 1
    assert switched.json()["llm_temperature"] == 0.85
    assert switched.json()["tts_speech_rate"] == 1.25
    for field in ("llm_temperature", "tts_speech_rate"):
        response = client.patch(path, headers=user, json={field: 1.1})
        assert response.status_code == 422
        assert response.json()["code"] == "VOICE_PARAMETER_UNSUPPORTED"
    assert (
        client.patch(path, headers=user, json={"tools": {"current_time": True}}).status_code == 200
    )
    switched_back = client.patch(
        path, headers=user, json={"model_preset_id": "fast-chat", "voice_preset_id": "cherry"}
    ).json()
    assert switched_back["llm_temperature"] == 0.85
    assert switched_back["tts_speech_rate"] == 1.25


def test_voice_filter_and_server_compatibility_reject_mixed_routes(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    user = _user_headers(client)
    _enable_doubao(client, _staff_headers(client, admin_headers))
    voices = client.get("/v1/voice-presets?model_preset_id=doubao-realtime", headers=user)
    assert voices.status_code == 200
    assert [(item["id"], item["provider"]) for item in voices.json()] == [("doubao-vv", "doubao")]
    old_voices = client.get("/v1/voice-presets?model_preset_id=fast-chat", headers=user).json()
    assert {item["id"] for item in old_voices} == {"cherry", "ethan"}
    for model, voice in (("fast-chat", "doubao-vv"), ("doubao-realtime", "cherry")):
        response = client.post(
            "/v1/agents",
            headers=user,
            json={"name": "错误组合", "model_preset_id": model, "voice_preset_id": voice},
        )
        assert response.status_code == 422
    old_client = client.post(
        "/v1/agents",
        headers=user,
        json={
            "name": "旧客户端",
            "model_preset_id": "fast-chat",
            "voice_preset_id": "cherry",
            "llm_temperature": 0.6,
            "tts_speech_rate": 1.0,
        },
    )
    assert old_client.status_code == 200, old_client.text


def test_doubao_enable_requires_deployment_and_production_validation(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    staff = _staff_headers(client, admin_headers)
    path = "/v1/admin/model-presets/doubao-realtime"
    payload = {"enabled": True, "is_default": True, "confirm": True}
    response = client.patch(path, headers=staff, json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "VOICE_ROUTE_NOT_VALIDATED"
    client.app.state.settings.doubao_realtime_enabled = True
    client.app.state.settings.doubao_api_key = "test-key"
    client.app.state.settings.app_env = "production"
    assert client.patch(path, headers=staff, json=payload).status_code == 422
    client.app.state.settings.doubao_realtime_validated = True
    assert client.patch(path, headers=staff, json=payload).status_code == 200


def test_unknown_s2s_cost_is_preserved_and_billing_event_is_unique(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    details = ProviderUsageDetails.model_validate(
        {
            "meters": [{"kind": "input_audio", "unit": "tokens", "quantity": 64}],
            "provider_usage": {"input_tokens": 64, "output_tokens_details": {"audio_tokens": 40}},
        }
    )

    async def record() -> None:
        async with client.app.state.session_factory() as session:
            device = await session.get(Device, owned["device_id"])
            values = dict(
                user_id=device.owner_user_id,
                device_id=device.id,
                provider="doubao",
                model="1.2.6.1",
                operation="realtime_s2s",
                cost_micros=None,
                cost_status="unknown",
                billing_event_key="doubao:request:usage1",
                provider_request_id="request",
                usage_details_json=details.model_dump_json(),
            )
            session.add(ProviderUsage(**values))
            await session.commit()
            stored = await session.scalar(select(ProviderUsage))
            assert stored.cost_micros is None
            assert json.loads(stored.usage_details_json)["provider_usage"]["input_tokens"] == 64
            session.add(ProviderUsage(**values))
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    asyncio.run(record())
    user = {"Authorization": f"Bearer {owned['user_token']}"}
    summary = client.get("/v1/account/usage", headers=user).json()
    assert summary["unknown_cost_records"] == summary["realtime_s2s_requests"] == 1
    assert summary["provider_cost_micros"] == 0  # Known subtotal, explicitly incomplete.
    assert summary["pricing_configured"] is False
    assert summary["asr_units"] == summary["tts_units"] == 0
    staff = _staff_headers(client, admin_headers)
    metrics = client.get("/v1/admin/metrics", headers=staff).json()
    assert metrics["unknown_cost_records_30d"] == 1
    assert (
        client.get("/v1/admin/provider-usage", headers=staff).json()[0]["unknown_cost_records"] == 1
    )


@pytest.mark.parametrize("value", ["transcript", True, -1, float("inf"), {"text": "hello"}])
def test_provider_usage_rejects_content_and_non_numeric_counters(value: object) -> None:
    with pytest.raises(ValidationError):
        ProviderUsageDetails(provider_usage={"input_tokens": value})
