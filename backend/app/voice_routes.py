"""Route contracts shared by the catalog, role API, and realtime gateway."""

from .models import ModelPreset, VoicePreset
from .schemas import RouteCapabilities
from .voice_inventory import QWEN_VOICE_MODELS, VOLC_S2S_VOICES, VOLC_TTS_VOICES

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
# Official full-duplex catalog: docs.volcengine.com/docs/6561/1257544 (2026-09-09).
DOUBAO_VOICE_CANDIDATES = (
    ("doubao-vv-2", "zh_female_vv_uranus_bigtts", "Vivi 2.0 / 豆包女声"),
    ("doubao-xiaohe-2", "zh_female_xiaohe_uranus_bigtts", "小何 2.0 / 豆包女声"),
    ("doubao-yunzhou-2", "zh_male_m191_uranus_bigtts", "云舟 2.0 / 豆包男声"),
    ("doubao-xiaotian-2", "zh_male_taocheng_uranus_bigtts", "小天 2.0 / 豆包男声"),
)
DOUBAO_VOICES = VOLC_S2S_VOICES | {DOUBAO_VOICE}
QWEN_INSTRUCT_MODEL = "qwen3-tts-instruct-flash-realtime-2026-01-22"
QWEN_INSTRUCT_VOICES = frozenset(
    voice for voice, models in QWEN_VOICE_MODELS.items() if QWEN_INSTRUCT_MODEL in models
)


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


def route_capabilities(
    model: ModelPreset, *, managed_role_overrides: bool = False,
) -> RouteCapabilities:
    cascade = model.route_kind == "cascade"
    return RouteCapabilities(
        llm_temperature=cascade,
        tts_speech_rate=cascade or model.route_kind == "managed_app",
        tools=model.route_kind != "managed_app",
        system_prompt=model.route_kind != "managed_app" or managed_role_overrides,
        history=model.route_kind != "managed_app",
        supported_tool_ids=list(CASCADE_TOOL_IDS) if model.route_kind != "managed_app" else [],
    )


def voice_is_compatible(model: ModelPreset, voice: VoicePreset) -> bool:
    if model.route_kind == "managed_app":
        return voice.provider == "aliyun-dialog" and voice.voice in {
            "application-default", "longanhuan", "longanyang",
        }
    if model.route_kind == "realtime_s2s":
        return (
            model.realtime_provider == voice.provider == "doubao"
            and model.realtime_model == DOUBAO_MODEL
            and voice.voice in DOUBAO_VOICES
        )
    if model.route_kind == "cascade" and model.tts_provider:
        if model.tts_provider == "volc-tts":
            return (model.tts_model == "seed-tts-2.0" and voice.provider == "volc-tts"
                    and voice.voice in VOLC_TTS_VOICES)
        if model.tts_model == QWEN_INSTRUCT_MODEL:
            return voice.provider == "dashscope" and voice.voice in QWEN_INSTRUCT_VOICES
        if model.tts_provider in {"dashscope", "dashscope-batch"}:
            return (voice.provider == "dashscope"
                    and model.tts_model in QWEN_VOICE_MODELS.get(voice.voice, ()))
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


def validate_tts_admission(settings, provider: str, model: str) -> None:
    if provider == "volc-tts":
        if model != "seed-tts-2.0" or not settings.volc_tts_api_key.strip():
            raise ValueError("volc TTS requires a supported model and credential")
        if not settings.volc_tts_url.startswith("https://"):
            raise ValueError("volc TTS endpoint must use HTTPS")
        if settings.app_env == "production" and not settings.volc_tts_validated:
            raise ValueError("volc TTS has not passed release validation")
    elif model.startswith("qwen3-tts-instruct-flash-realtime"):
        if provider != "dashscope" or not settings.tts_api_key.strip():
            raise ValueError("instruct TTS requires a supported provider and credential")
        if not settings.qwen_realtime_tts_url.startswith("wss://"):
            raise ValueError("instruct TTS endpoint must use WSS")
        if settings.app_env == "production" and not settings.qwen_instruct_validated:
            raise ValueError("instruct TTS has not passed release validation")


def validate_route_admission(model, settings) -> None:
    if model.route_kind == "cascade":
        validate_tts_admission(settings, model.tts_provider, model.tts_model)
        if model.asr_provider == "volc-asr":
            if (model.asr_model != "bigmodel" or not settings.volc_asr_api_key.strip()
                    or not settings.volc_asr_url.startswith("wss://")
                    or (settings.app_env == "production" and not settings.volc_asr_validated)):
                raise ValueError("volc ASR has not passed validation")
    elif model.route_kind == "managed_app":
        if not managed_route_available(settings) or not settings.aliyun_dialog_url.startswith("wss://"):
            raise ValueError("Aliyun route has not passed validation")
    elif model.route_kind == "realtime_s2s":
        if (not settings.doubao_realtime_enabled or not settings.doubao_api_key.strip()
                or not settings.doubao_realtime_url.startswith("wss://")
                or (settings.app_env == "production" and not settings.doubao_realtime_validated)):
            raise ValueError("doubao route has not passed release validation")
