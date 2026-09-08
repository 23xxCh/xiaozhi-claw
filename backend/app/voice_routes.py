"""Route contracts shared by the catalog, role API, and realtime gateway."""

from .models import ModelPreset, VoicePreset
from .schemas import RouteCapabilities

CASCADE_FIELDS = (
    "asr_provider",
    "asr_model",
    "llm_provider",
    "llm_model",
    "tts_provider",
    "tts_model",
)
CASCADE_TOOL_IDS = (
    "current_time",
    "calculator",
    "weather",
    "web_search",
    "self.audio_speaker.set_volume",
    "self.screen.set_brightness",
)
DOUBAO_MODEL = "1.2.6.1"
DOUBAO_VOICE = "zh_female_vv_jupiter_bigtts"
QWEN_INSTRUCT_MODEL = "qwen3-tts-instruct-flash-realtime-2026-01-22"
QWEN_INSTRUCT_VOICES = frozenset({
    "Cherry", "Ethan", "Serena", "Chelsie", "Momo", "Vivian", "Moon",
    "Maia", "Kai", "Eldric Sage", "Mia", "Vincent",
})


def validate_model_route(model: ModelPreset) -> None:
    if model.route_kind == "cascade":
        if any(not getattr(model, field) for field in CASCADE_FIELDS):
            raise ValueError("cascade route requires ASR, LLM and TTS provider/model")
        if model.realtime_provider is not None or model.realtime_model is not None:
            raise ValueError("cascade route cannot contain a realtime provider/model")
    elif model.route_kind == "managed_app":
        if (model.realtime_provider != "aliyun-dialog"
                or model.realtime_model != "multimodal-dialog"
                or any(getattr(model, field) is not None for field in CASCADE_FIELDS)):
            raise ValueError("managed application route is unsupported")
    elif model.route_kind == "realtime_s2s":
        if model.realtime_provider != "doubao" or model.realtime_model != DOUBAO_MODEL:
            raise ValueError("realtime route provider/model is unsupported")
        if any(getattr(model, field) is not None for field in CASCADE_FIELDS):
            raise ValueError("realtime route cannot contain separate ASR, LLM or TTS providers")
    else:
        raise ValueError("voice route is unsupported")


def route_capabilities(model: ModelPreset) -> RouteCapabilities:
    cascade = model.route_kind == "cascade"
    return RouteCapabilities(
        llm_temperature=cascade,
        tts_speech_rate=cascade,
        tools=model.route_kind != "managed_app",
        system_prompt=model.route_kind != "managed_app",
        history=model.route_kind != "managed_app",
        supported_tool_ids=list(CASCADE_TOOL_IDS) if model.route_kind != "managed_app" else [],
    )


def voice_is_compatible(model: ModelPreset, voice: VoicePreset) -> bool:
    if model.route_kind == "managed_app":
        return voice.provider == "aliyun-dialog" and voice.voice == "application-default"
    if model.route_kind == "realtime_s2s":
        return (
            model.realtime_provider == voice.provider == "doubao"
            and model.realtime_model == DOUBAO_MODEL
            and voice.voice == DOUBAO_VOICE
        )
    if model.route_kind == "cascade" and model.tts_provider:
        if model.tts_provider == "volc-tts":
            return (model.tts_model == "seed-tts-2.0" and voice.provider == "volc-tts"
                    and voice.voice in {"zh_female_vv_uranus_bigtts",
                                        "zh_female_xiaohe_uranus_bigtts",
                                        "zh_male_m191_uranus_bigtts"})
        if model.tts_model == QWEN_INSTRUCT_MODEL:
            return voice.provider == "dashscope" and voice.voice in QWEN_INSTRUCT_VOICES
        return voice.provider == model.tts_provider.removesuffix("-batch")
    return False


def compatible_voices(model: ModelPreset, voices: list[VoicePreset]) -> list[VoicePreset]:
    return sorted(
        (voice for voice in voices if voice.enabled and voice_is_compatible(model, voice)),
        key=lambda voice: (not voice.is_default, voice.id),
    )


def managed_route_available(settings) -> bool:
    return bool(
        settings.aliyun_dialog_enabled and settings.aliyun_dialog_api_key
        and settings.aliyun_dialog_workspace_id and settings.aliyun_dialog_app_id
        and (settings.app_env != "production" or settings.aliyun_dialog_validated)
    )
