from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Agent, Device, ModelPreset, User, VoicePreset
from .usage_profiles import ensure_adult_profile

DEFAULT_MODEL_PRESETS = (
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
)

DEFAULT_VOICE_PRESETS = (
    {
        "id": "cherry",
        "display_name": "Cherry / 温暖女声",
        "language": "zh-CN",
        "provider": "dashscope",
        "voice": "Cherry",
        "is_default": True,
    },
    {
        "id": "ethan",
        "display_name": "Ethan / 沉稳男声",
        "language": "zh-CN",
        "provider": "dashscope",
        "voice": "Ethan",
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
        agent = Agent(owner_user_id=user.id, usage_profile_id=profile.id, name="我的助手")
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
