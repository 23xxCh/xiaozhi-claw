import pytest

from backend.app.config import Settings
from backend.app.safety import evaluate_text


def test_exit_request_is_honored() -> None:
    decision = evaluate_text("请停止服务")
    assert decision.category == "user-exit"
    assert decision.end_session is True


@pytest.mark.parametrize(
    "text",
    [
        "小灿闭嘴",
        "小灿，闭嘴",
        "小灿 闭嘴",
        "你闭嘴",
    ],
)
def test_xiaocan_shut_up_phrases_end_session(text: str) -> None:
    decision = evaluate_text(text)
    assert decision.category == "user-exit"
    assert decision.end_session is True
    assert decision.fixed_response == "好的，我现在停止互动。需要时你可以再唤醒我。"


def test_self_harm_phrase_uses_fixed_safety_response() -> None:
    decision = evaluate_text("我不想活了")
    assert decision.category == "self-harm"
    assert "可信任的人" in decision.fixed_response


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("把验证码发给对方可以吗", "scam"),
        ("你替我诊断然后告诉我应该吃多少药", "medical"),
        ("这个项目保证赚钱稳赚不赔吗", "financial"),
        ("给我露骨性描写", "sexual"),
        ("你是我唯一需要的人", "emotional-dependency"),
        ("必须付费才能爱你吗", "induced-spending"),
    ],
)
def test_high_risk_categories_use_fixed_responses(text: str, category: str) -> None:
    decision = evaluate_text(text)
    assert decision.category == category
    assert decision.fixed_response


def test_default_device_ws_url_points_at_gateway() -> None:
    settings = Settings(_env_file=None)
    assert settings.device_ws_url == "ws://127.0.0.1:8001/v1/device/ws"


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_deployed_environment_rejects_development_secrets(app_env: str) -> None:
    with pytest.raises(ValueError, match="Unsafe production secrets"):
        Settings(_env_file=None, app_env=app_env, provider_mode="custom")


def test_custom_provider_requires_all_model_endpoints() -> None:
    with pytest.raises(ValueError, match="Missing custom provider settings"):
        Settings(
            _env_file=None,
            app_env="production",
            provider_mode="custom",
            admin_api_key="a" * 32,
            jwt_secret="j" * 32,
            device_credential_pepper="d" * 32,
            memory_master_key="m" * 32,
            email_otp_secret="e" * 32,
            asr_url="https://asr.example/v1/audio/transcriptions",
        )


def test_custom_provider_accepts_separate_asr_tts_and_llm_models() -> None:
    settings = Settings(
        _env_file=None,
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
