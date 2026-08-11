import pytest

from backend.app.config import Settings
from backend.app.safety import evaluate_text


def test_exit_request_is_honored() -> None:
    decision = evaluate_text("请停止服务")
    assert decision.category == "user-exit"
    assert decision.end_session is True


def test_self_harm_phrase_uses_fixed_safety_response() -> None:
    decision = evaluate_text("我不想活了")
    assert decision.category == "self-harm"
    assert "可信任的人" in decision.fixed_response


def test_production_rejects_development_secrets() -> None:
    with pytest.raises(ValueError, match="Unsafe production secrets"):
        Settings(app_env="production", provider_mode="custom")


def test_custom_provider_requires_all_model_endpoints() -> None:
    with pytest.raises(ValueError, match="Missing custom provider settings"):
        Settings(provider_mode="custom", asr_url="https://asr.example/v1/audio/transcriptions")


def test_custom_provider_accepts_separate_asr_tts_and_llm_models() -> None:
    settings = Settings(
        provider_mode="custom",
        asr_url="https://asr.example/v1/audio/transcriptions",
        asr_api_key="asr-secret",
        asr_model="asr-model",
        tts_url="https://tts.example/v1/audio/speech",
        tts_api_key="tts-secret",
        tts_model="tts-model",
        tts_voice="voice-1",
        llm_url="https://llm.example/v1/chat/completions",
        llm_api_key="llm-secret",
        llm_model="llm-model",
    )
    assert settings.provider_mode == "custom"
    assert settings.asr_model == "asr-model"
    assert settings.tts_model == "tts-model"
    assert settings.llm_model == "llm-model"
