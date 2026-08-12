from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./hensun-desk.db"
    admin_api_key: str = "development-admin-change-me"
    jwt_secret: str = "development-jwt-change-me"
    device_credential_pepper: str = "development-device-pepper-change-me"
    memory_master_key: str = "development-memory-key-change-me"
    provider_mode: Literal["mock", "custom"] = "mock"
    provider_timeout_seconds: float = 30
    ffmpeg_path: str = "ffmpeg"

    asr_protocol: Literal["openai-transcriptions", "qwen-chat-completions"] = (
        "openai-transcriptions"
    )
    asr_url: str = ""
    asr_api_key: str = ""
    asr_model: str = ""
    tts_protocol: Literal["openai-speech", "dashscope-generation"] = "openai-speech"
    tts_url: str = ""
    tts_api_key: str = ""
    tts_model: str = ""
    tts_voice: str = ""
    tts_language_type: str = "Chinese"
    tts_response_format: str = "mp3"
    llm_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    ota_signing_public_key: str = ""
    ota_base_url: str = "https://api.hensun.invalid/v1/ota/"
    device_ws_url: str = "ws://127.0.0.1:8000/v1/device/ws"
    claim_ttl_seconds: int = 600
    trial_days: int = 30
    trial_monthly_turns: int = 600
    free_monthly_turns: int = 60
    gateway_id: str = "gateway-local-1"
    command_poll_interval_seconds: float = 0.5
    device_offline_after_seconds: int = 90
    max_device_audio_queue_frames: int = 100
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"
    web_app_url: str = "http://localhost:3000"
    session_cookie_secure: bool = False
    wechat_web_app_id: str = ""
    wechat_web_app_secret: str = ""
    wechat_web_redirect_uri: str = ""
    qwen_realtime_asr_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    qwen_realtime_tts_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    qwen_realtime_asr_model: str = "qwen3-asr-flash-realtime"
    qwen_realtime_tts_model: str = "qwen3-tts-flash-realtime"
    fallback_enabled: bool = True
    fallback_api_key: str = ""
    fallback_asr_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    fallback_asr_model: str = "qwen3-asr-flash"
    fallback_llm_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    fallback_llm_model: str = "qwen3.7-flash"
    fallback_tts_url: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "multimodal-generation/generation"
    )
    fallback_tts_model: str = "qwen3-tts-flash"
    fallback_tts_voice: str = "Cherry"

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @model_validator(mode="after")
    def reject_unsafe_production_defaults(self) -> "Settings":
        if self.app_env == "production":
            unsafe = {
                "admin_api_key": self.admin_api_key,
                "jwt_secret": self.jwt_secret,
                "device_credential_pepper": self.device_credential_pepper,
                "memory_master_key": self.memory_master_key,
            }
            bad = [
                name for name, value in unsafe.items() if "change-me" in value or len(value) < 24
            ]
            if bad:
                raise ValueError(f"Unsafe production secrets: {', '.join(bad)}")

        if self.provider_mode == "custom":
            custom = {
                "asr_url": self.asr_url,
                "asr_api_key": self.asr_api_key,
                "asr_model": self.asr_model,
                "tts_url": self.tts_url,
                "tts_api_key": self.tts_api_key,
                "tts_model": self.tts_model,
                "tts_voice": self.tts_voice,
                "llm_url": self.llm_url,
                "llm_api_key": self.llm_api_key,
                "llm_model": self.llm_model,
            }
            missing = [name for name, value in custom.items() if not value.strip()]
            if missing:
                raise ValueError(f"Missing custom provider settings: {', '.join(missing)}")

        if self.app_env != "production":
            return self
        if self.provider_mode != "custom":
            raise ValueError("Production must use custom AI providers")
        if self.fallback_enabled and not (self.fallback_api_key or self.asr_api_key):
            raise ValueError("Production fallback requires a DashScope API key")
        provider_urls = [self.asr_url, self.tts_url, self.llm_url]
        if any(not url.startswith("https://") for url in provider_urls):
            raise ValueError("Production model endpoints must use HTTPS")
        if ".invalid" in self.ota_base_url:
            raise ValueError("OTA_BASE_URL must be a real HTTPS endpoint in production")
        if not self.device_ws_url.startswith("wss://"):
            raise ValueError("DEVICE_WS_URL must use WSS in production")
        if not self.ota_signing_public_key:
            raise ValueError("OTA_SIGNING_PUBLIC_KEY is required in production")
        if not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SECURE must be enabled in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
