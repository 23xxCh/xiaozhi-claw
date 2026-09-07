from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./hensun-desk.db"
    admin_api_key: str = "development-admin-change-me"
    jwt_secret: str = "development-jwt-change-me"
    device_credential_pepper: str = "development-device-pepper-change-me"
    memory_master_key: str = "development-memory-key-change-me"
    provider_mode: Literal["mock", "custom"] = "mock"
    provider_timeout_seconds: float = 30
    provider_host_overrides: str = ""
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
    web_search_mcp_enabled: bool = False
    web_search_mcp_url: str = (
        "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
    )
    web_search_mcp_api_key: str = ""
    web_search_qwen_enabled: bool = False
    web_search_qwen_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    web_search_qwen_model: str = "qwen-plus"

    ota_signing_public_key: str = ""
    ota_base_url: str = "https://api.hensun.invalid/v1/ota/"
    device_ws_url: str = "ws://127.0.0.1:8001/v1/device/ws"
    claim_ttl_seconds: int = 600
    trial_days: int = 30
    trial_monthly_turns: int = 600
    free_monthly_turns: int = 60
    gateway_id: str = "gateway-local-1"
    command_poll_interval_seconds: float = 0.5
    device_offline_after_seconds: int = 90
    device_ws_activity_timeout_seconds: float = 45.0
    # ESP32 sends one Opus frame every 60 ms. Keep one minute bounded in memory;
    # the old value of 100 discarded the utterance after only six seconds.
    max_device_audio_queue_frames: int = 1000
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"
    web_app_url: str = "http://localhost:3000"
    session_cookie_secure: bool = False
    wechat_web_app_id: str = ""
    wechat_web_app_secret: str = ""
    wechat_web_redirect_uri: str = ""
    email_delivery_mode: Literal["development", "smtp"] = "development"
    email_otp_secret: str = "development-email-otp-change-me"
    email_otp_ttl_seconds: int = 600
    email_otp_resend_seconds: int = 60
    email_otp_max_attempts: int = 5
    email_ip_request_limit: int = 20
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    qwen_realtime_asr_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    qwen_realtime_tts_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    qwen_realtime_asr_model: str = "qwen3-asr-flash-realtime"
    qwen_realtime_vad_silence_ms: int = 700
    qwen_realtime_tts_model: str = "qwen3-tts-flash-realtime"
    doubao_realtime_enabled: bool = False
    doubao_realtime_validated: bool = False
    doubao_api_key: str = Field(default="", repr=False)
    doubao_realtime_url: str = "wss://openspeech.bytedance.com/api/v3/duplex/realtime/dialogue"
    fallback_enabled: bool = True
    fallback_api_key: str = ""
    fallback_asr_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    fallback_asr_model: str = "qwen3-asr-flash"
    fallback_llm_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    fallback_llm_model: str = "qwen3.7-flash"
    fallback_tts_url: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    )
    fallback_tts_model: str = "qwen3-tts-flash"
    fallback_tts_voice: str = "Cherry"
    family_mode_enabled: bool = False
    family_mode_openid_whitelist: str = ""

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def family_mode_allowed_openids(self) -> set[str]:
        return {
            item.strip() for item in self.family_mode_openid_whitelist.split(",") if item.strip()
        }

    @model_validator(mode="after")
    def reject_unsafe_production_defaults(self) -> "Settings":
        if self.doubao_realtime_enabled:
            if not self.doubao_api_key.strip():
                raise ValueError("DOUBAO_API_KEY is required when the route is enabled")
            if not self.doubao_realtime_url.startswith("wss://"):
                raise ValueError("DOUBAO_REALTIME_URL must use WSS")
            if self.app_env == "production" and not self.doubao_realtime_validated:
                raise ValueError("Doubao route requires real-device release validation")
        if self.app_env in {"staging", "production"}:
            unsafe = {
                "admin_api_key": self.admin_api_key,
                "jwt_secret": self.jwt_secret,
                "device_credential_pepper": self.device_credential_pepper,
                "memory_master_key": self.memory_master_key,
                "email_otp_secret": self.email_otp_secret,
            }
            bad = [
                name for name, value in unsafe.items() if "change-me" in value or len(value) < 24
            ]
            if bad:
                raise ValueError(f"Unsafe production secrets: {', '.join(bad)}")

        if self.app_env not in {"staging", "production"}:
            return self
        if self.provider_mode != "custom":
            raise ValueError("Production must use custom AI providers")
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
        if self.web_search_mcp_enabled:
            if not self.web_search_mcp_url.startswith("https://"):
                raise ValueError("WEB_SEARCH_MCP_URL must use HTTPS in production")
            if not (self.web_search_mcp_api_key or self.asr_api_key):
                raise ValueError(
                    "WEB_SEARCH_MCP_API_KEY or an Alibaba ASR key is required "
                    "when search is enabled"
                )
        if self.web_search_qwen_enabled:
            if not self.web_search_qwen_url.startswith("https://"):
                raise ValueError("WEB_SEARCH_QWEN_URL must use HTTPS in production")
            if not (self.web_search_mcp_api_key or self.asr_api_key):
                raise ValueError(
                    "WEB_SEARCH_MCP_API_KEY or an Alibaba ASR key is required "
                    "when Qwen search is enabled"
                )
        if self.email_delivery_mode != "smtp":
            raise ValueError("Production email login must use SMTP delivery")
        smtp_required = {
            "smtp_host": self.smtp_host,
            "smtp_username": self.smtp_username,
            "smtp_password": self.smtp_password,
            "smtp_from_email": self.smtp_from_email,
        }
        smtp_missing = [name for name, value in smtp_required.items() if not value.strip()]
        if smtp_missing:
            raise ValueError(f"Missing SMTP settings: {', '.join(smtp_missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
