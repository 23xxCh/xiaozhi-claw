from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Agent, Device, ModelPreset, User, VoicePreset
from .usage_profiles import ensure_adult_profile
from .voice_routes import (
    DOUBAO_VOICE_CANDIDATES,
    QWEN_INSTRUCT_MODEL,
    compatible_voices,
    validate_model_route,
)

DEFAULT_MODEL_PRESETS = (
    {
        "id": "volc-tts-chat", "display_name": "火山音色（验证候选）",
        "description": "阿里识别＋DeepSeek＋火山 TTS 2.0；独立音色，真机效果待验收",
        "asr_provider": "dashscope", "asr_model": "qwen3-asr-flash-realtime",
        "llm_provider": "deepseek", "llm_model": "deepseek-v4-flash",
        "tts_provider": "volc-tts", "tts_model": "seed-tts-2.0",
        "asr_cost_micros_per_minute": 19_800,
        "llm_input_cost_micros_per_million_tokens": 1_000_000,
        "llm_output_cost_micros_per_million_tokens": 2_000_000,
        "tts_cost_micros_per_10k_chars": 5_000_000,
        "enabled": False, "is_default": False,
    },
    {
        "id": "expressive-chat",
        "display_name": "情绪语音（验证候选）",
        "description": "阿里识别＋DeepSeek＋Qwen 情绪语音；声音和屏幕共享情绪，支持 12 种音色",
        "asr_provider": "dashscope", "asr_model": "qwen3-asr-flash-realtime",
        "llm_provider": "deepseek", "llm_model": "deepseek-v4-flash",
        "tts_provider": "dashscope", "tts_model": QWEN_INSTRUCT_MODEL,
        "asr_cost_micros_per_minute": 19_800,
        "llm_input_cost_micros_per_million_tokens": 1_000_000,
        "llm_output_cost_micros_per_million_tokens": 2_000_000,
        "tts_cost_micros_per_10k_chars": 1_000_000,
        "enabled": False, "is_default": False,
    },
    {
        "id": "fast-chat",
        "display_name": "快速对话",
        "description": "低延迟日常对话",
        "asr_provider": "dashscope",
        "asr_model": "qwen3-asr-flash-realtime",
        "llm_provider": "deepseek",
        "llm_model": "deepseek-v4-flash",
        "tts_provider": "dashscope",
        "tts_model": "qwen3-tts-flash-realtime",
        "asr_cost_micros_per_minute": 19_800,
        "llm_input_cost_micros_per_million_tokens": 1_000_000,
        "llm_output_cost_micros_per_million_tokens": 2_000_000,
        "tts_cost_micros_per_10k_chars": 1_000_000,
        "is_default": True,
    },
    {
        "id": "rich-chat",
        "display_name": "丰富表达",
        "description": "更丰富的角色表达，响应稍慢",
        "asr_provider": "dashscope",
        "asr_model": "qwen3-asr-flash-realtime",
        "llm_provider": "deepseek",
        "llm_model": "deepseek-v4-pro",
        "tts_provider": "dashscope",
        "tts_model": "qwen3-tts-flash-realtime",
        "asr_cost_micros_per_minute": 19_800,
        "llm_input_cost_micros_per_million_tokens": 3_000_000,
        "llm_output_cost_micros_per_million_tokens": 6_000_000,
        "tts_cost_micros_per_10k_chars": 1_000_000,
        "is_default": False,
    },
    {
        "id": "stable-fallback",
        "display_name": "稳定备用",
        "description": "服务降级时自动使用，不建议手动选择",
        "asr_provider": "dashscope-batch",
        "asr_model": "qwen3-asr-flash",
        "llm_provider": "dashscope",
        "llm_model": "qwen3.7-flash",
        "tts_provider": "dashscope-batch",
        "tts_model": "qwen3-tts-flash",
        "asr_cost_micros_per_minute": 13_200,
        "llm_input_cost_micros_per_million_tokens": 200_000,
        "llm_output_cost_micros_per_million_tokens": 800_000,
        "tts_cost_micros_per_10k_chars": 800_000,
        "enabled": False,
        "is_default": False,
    },
    {
        "id": "doubao-realtime",
        "display_name": "豆包实时语音",
        "description": "端到端语音对话，支持已开放的工具；表达灵活度和语速固定",
        "route_kind": "realtime_s2s",
        "realtime_provider": "doubao",
        "realtime_model": "1.2.6.1",
        "asr_provider": None,
        "asr_model": None,
        "llm_provider": None,
        "llm_model": None,
        "tts_provider": None,
        "tts_model": None,
        "asr_cost_micros_per_minute": 0,
        "llm_input_cost_micros_per_million_tokens": 0,
        "llm_output_cost_micros_per_million_tokens": 0,
        "tts_cost_micros_per_10k_chars": 0,
        "enabled": False,
        "is_default": False,
    },
    {
        "id": "aliyun-dialog",
        "display_name": "阿里多模态应用（验证候选）",
        "description": "使用阿里应用的人设、模型和默认音色；暂不传入本地人设及历史，每轮独立",
        "route_kind": "managed_app",
        "realtime_provider": "aliyun-dialog",
        "realtime_model": "multimodal-dialog",
        "asr_provider": None,
        "asr_model": None,
        "llm_provider": None,
        "llm_model": None,
        "tts_provider": None,
        "tts_model": None,
        "asr_cost_micros_per_minute": 0,
        "llm_input_cost_micros_per_million_tokens": 0,
        "llm_output_cost_micros_per_million_tokens": 0,
        "tts_cost_micros_per_10k_chars": 0,
        "enabled": False,
        "is_default": False,
    },
)

DEFAULT_VOICE_PRESETS = (
    *(
        {"id": id, "display_name": label, "language": "zh-CN",
         "provider": "doubao", "voice": voice, "is_default": False, "enabled": False}
        for id, voice, label in DOUBAO_VOICE_CANDIDATES
    ),
    *(
        {"id": f"volc-{id}", "display_name": label, "language": "zh-CN",
         "provider": "volc-tts", "voice": voice, "is_default": False,
         "preview_url": f"/voice-previews/volc-{id}.wav"}
        for id, voice, label in (
            ("vv", "zh_female_vv_uranus_bigtts", "VV 2.0 / 火山女声"),
            ("xiaohe", "zh_female_xiaohe_uranus_bigtts", "小何 2.0 / 火山女声"),
            ("yunzhou", "zh_male_m191_uranus_bigtts", "M191 2.0 / 火山男声"),
        )
    ),
    *(
        {"id": id, "display_name": label, "language": "zh-CN",
         "provider": "dashscope", "voice": voice, "is_default": False,
         "preview_url": f"/voice-previews/{id}.wav"}
        for id, voice, label in (
            ("serena", "Serena", "Serena / 温柔女声"),
            ("chelsie", "Chelsie", "Chelsie / 二次元女声"),
            ("momo", "Momo", "Momo / 撒娇搞怪女声"),
            ("vivian", "Vivian", "Vivian / 俏皮小暴躁女声"),
            ("moon", "Moon", "Moon / 率性男声"),
            ("maia", "Maia", "Maia / 知性温柔女声"),
            ("kai", "Kai", "Kai / 舒缓男声"),
            ("eldric-sage", "Eldric Sage", "沧明子 / 沉稳老者"),
            ("mia", "Mia", "Mia / 乖巧女声"),
            ("vincent", "Vincent", "Vincent / 沙哑男声"),
        )
    ),
    {
        "id": "cherry",
        "display_name": "Cherry / 温暖女声",
        "language": "zh-CN",
        "provider": "dashscope",
        "voice": "Cherry",
        "preview_url": "/voice-previews/cherry.mp3",
        "is_default": True,
    },
    {
        "id": "ethan",
        "display_name": "Ethan / 沉稳男声",
        "language": "zh-CN",
        "provider": "dashscope",
        "voice": "Ethan",
        "preview_url": "/voice-previews/ethan.mp3",
        "is_default": False,
    },
    {
        "id": "doubao-vv",
        "display_name": "VV / 豆包女声",
        "language": "zh-CN",
        "provider": "doubao",
        "voice": "zh_female_vv_jupiter_bigtts",
        "is_default": False,
    },
    {
        "id": "aliyun-app-default",
        "display_name": "阿里应用默认音色",
        "language": "zh-CN",
        "provider": "aliyun-dialog",
        "voice": "application-default",
        "is_default": False,
    },
)


async def ensure_catalog(session: AsyncSession) -> None:
    for values in DEFAULT_MODEL_PRESETS:
        preset = await session.get(ModelPreset, values["id"])
        if preset is None:
            session.add(ModelPreset(**values))
        elif not any(
            (
                preset.asr_cost_micros_per_minute,
                preset.llm_input_cost_micros_per_million_tokens,
                preset.llm_output_cost_micros_per_million_tokens,
                preset.tts_cost_micros_per_10k_chars,
            )
        ):
            preset.asr_cost_micros_per_minute = values["asr_cost_micros_per_minute"]
            preset.llm_input_cost_micros_per_million_tokens = values[
                "llm_input_cost_micros_per_million_tokens"
            ]
            preset.llm_output_cost_micros_per_million_tokens = values[
                "llm_output_cost_micros_per_million_tokens"
            ]
            preset.tts_cost_micros_per_10k_chars = values["tts_cost_micros_per_10k_chars"]
    for values in DEFAULT_VOICE_PRESETS:
        if await session.get(VoicePreset, values["id"]) is None:
            session.add(VoicePreset(**values))
    await session.flush()


async def ensure_default_agent(session: AsyncSession, user: User) -> Agent:
    await ensure_catalog(session)
    profile = await ensure_adult_profile(session, user)
    agent = await session.scalar(
        select(Agent).where(Agent.owner_user_id == user.id).order_by(Agent.created_at)
    )
    if agent is None:
        model, voice = await resolve_agent_presets(session)
        agent = Agent(
            owner_user_id=user.id,
            usage_profile_id=profile.id,
            name="我的助手",
            model_preset_id=model.id,
            voice_preset_id=voice.id,
        )
        session.add(agent)
        await session.flush()
    devices = list(
        await session.scalars(
            select(Device).where(
                Device.owner_user_id == user.id,
                Device.active_agent_id.is_(None),
            )
        )
    )
    for device in devices:
        device.active_agent_id = agent.id
        device.active_profile_id = agent.usage_profile_id
    return agent


async def resolve_agent_presets(
    session: AsyncSession,
    model_preset_id: str | None = None,
    voice_preset_id: str | None = None,
) -> tuple[ModelPreset, VoicePreset]:
    """Resolve omitted choices from one catalog for creation and device onboarding."""
    if model_preset_id is None:
        model = await session.scalar(
            select(ModelPreset)
            .where(ModelPreset.enabled.is_(True))
            .order_by(ModelPreset.is_default.desc(), ModelPreset.id)
            .limit(1)
        )
    else:
        model = await session.get(ModelPreset, model_preset_id)
    if model is None or not model.enabled:
        raise ValueError("model preset unavailable")
    validate_model_route(model)
    voices = list(await session.scalars(select(VoicePreset).where(VoicePreset.enabled.is_(True))))
    compatible = compatible_voices(model, voices)
    voice = next(
        (item for item in compatible if voice_preset_id is None or item.id == voice_preset_id),
        None,
    )
    if voice is None:
        raise ValueError("voice preset is unavailable or incompatible with the selected model")
    return model, voice


async def validate_release_catalog(session: AsyncSession, settings) -> None:
    from .voice_routes import validate_route_admission

    if settings.app_env not in {"staging", "production"}:
        return
    models = list(await session.scalars(select(ModelPreset).where(ModelPreset.enabled.is_(True))))
    voices = list(await session.scalars(select(VoicePreset).where(VoicePreset.enabled.is_(True))))
    for model in models:
        validate_model_route(model)
        validate_route_admission(model, settings)
        if not compatible_voices(model, voices):
            raise ValueError("enabled model has no compatible voice")
