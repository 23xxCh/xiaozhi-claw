from collections.abc import AsyncIterator

import pytest

from backend.app.config import Settings
from backend.realtime.providers import (
    MockAsrSession,
    MockTtsSession,
    ProviderNotRegisteredError,
    RealtimeProviderBundle,
    create_realtime_providers,
)


class RegisteredLlm:
    async def reply_stream(
        self,
        transcript: str,
        history: list[dict[str, str]],
        memories: list[str],
        *,
        system_prompt: str,
        model: str,
        temperature: float,
        tools: list[dict[str, object]] | None = None,
        tool_executor=None,
    ) -> AsyncIterator[str]:
        del history, memories, system_prompt, temperature, tools, tool_executor
        yield f"{model}:{transcript}"


@pytest.mark.asyncio
async def test_new_provider_only_requires_registration_not_coordinator_changes() -> None:
    registry = RealtimeProviderBundle(Settings(provider_mode="mock"), mock=True)
    seen: list[tuple[str, str]] = []

    async def open_asr(model: str) -> MockAsrSession:
        seen.append(("asr", model))
        return MockAsrSession()

    async def open_tts(model: str, voice: str, speech_rate: float) -> MockTtsSession:
        seen.append(("tts", f"{model}:{voice}:{speech_rate}"))
        return MockTtsSession()

    llm = RegisteredLlm()
    registry.register_asr("vendor-x", open_asr)
    registry.register_llm("vendor-x", llm)
    registry.register_tts("vendor-x", open_tts)

    assert isinstance(await registry.open_asr_for("vendor-x", "asr-x"), MockAsrSession)
    assert registry.llm_for("vendor-x") is llm
    assert isinstance(
        await registry.open_tts_for("vendor-x", "tts-x", "voice-x", 1.1),
        MockTtsSession,
    )
    assert seen == [("asr", "asr-x"), ("tts", "tts-x:voice-x:1.1")]


def test_unknown_provider_fails_with_stable_kind_and_id() -> None:
    registry = RealtimeProviderBundle(Settings(provider_mode="mock"), mock=True)

    with pytest.raises(ProviderNotRegisteredError) as error:
        registry.llm_for("missing")

    assert error.value.kind == "llm"
    assert error.value.provider_id == "missing"


def test_mock_registry_supports_catalog_provider_ids() -> None:
    registry = create_realtime_providers(Settings(provider_mode="mock"))

    assert registry.llm_for("deepseek") is registry.llm
    assert registry.llm_for("dashscope") is not None


def test_settings_exposes_typed_runtime_groups_without_renaming_env_fields() -> None:
    settings = Settings(
        provider_mode="custom",
        database_url="sqlite+aiosqlite:///grouped.db",
        gateway_id="gateway-test",
        llm_url="https://llm.example/v1",
        llm_api_key="secret",
        llm_model="model-x",
    )

    assert settings.control_plane.database_url.endswith("grouped.db")
    assert settings.gateway.gateway_id == "gateway-test"
    assert settings.providers.realtime_llm.provider_id == "deepseek"
    assert settings.providers.realtime_llm.model == "model-x"
    assert settings.security.jwt_secret == settings.jwt_secret
