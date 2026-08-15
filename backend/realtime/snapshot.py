import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.catalog import ensure_default_agent
from backend.app.models import (
    Agent,
    AgentMemory,
    Device,
    ModelPreset,
    UsageProfile,
    UsageProfileKind,
    User,
    VoicePreset,
)
from backend.app.security import decrypt_memory


@dataclass(frozen=True)
class AgentSnapshot:
    agent_id: str
    usage_profile_id: str
    usage_profile_kind: str
    config_version: int
    system_prompt: str
    memory_consent: bool
    memories: list[str]
    asr_provider: str
    asr_model: str
    llm_provider: str
    llm_model: str
    tts_provider: str
    tts_model: str
    voice: str
    llm_temperature: float
    tts_speech_rate: float
    tools: dict[str, bool]
    asr_cost_micros_per_minute: int
    llm_input_cost_micros_per_million_tokens: int
    llm_output_cost_micros_per_million_tokens: int
    tts_cost_micros_per_10k_chars: int


async def load_snapshot(session: AsyncSession, device: Device, settings) -> AgentSnapshot:
    if device.active_agent_id is None:
        user = await session.get(User, device.owner_user_id)
        if user is None:
            raise RuntimeError("device owner is missing")
        agent = await ensure_default_agent(session, user)
        device.active_agent_id = agent.id
    else:
        agent = await session.get(Agent, device.active_agent_id)
    if agent is None:
        raise RuntimeError("active agent is missing")
    profile = await session.get(UsageProfile, device.active_profile_id or agent.usage_profile_id)
    if profile is None or profile.owner_user_id != device.owner_user_id:
        raise RuntimeError("active usage profile is missing")
    if agent.usage_profile_id != profile.id:
        replacement = await session.scalar(
            select(Agent).where(Agent.usage_profile_id == profile.id).order_by(Agent.created_at)
        )
        if replacement is None:
            raise RuntimeError("active usage profile has no assistant")
        agent = replacement
        device.active_agent_id = agent.id
    device.active_profile_id = profile.id
    model = await session.get(ModelPreset, agent.model_preset_id)
    voice = await session.get(VoicePreset, agent.voice_preset_id)
    if model is None or not model.enabled or voice is None or not voice.enabled:
        raise RuntimeError("agent preset is unavailable")
    memories: list[str] = []
    profile_memory_allowed = profile.kind == UsageProfileKind.ADULT.value or profile.memory_consent
    if agent.memory_consent and profile_memory_allowed:
        encrypted = list(
            await session.scalars(
                select(AgentMemory.encrypted_value).where(AgentMemory.agent_id == agent.id)
            )
        )
        memories = [decrypt_memory(value, settings) for value in encrypted]
    return AgentSnapshot(
        agent_id=agent.id,
        usage_profile_id=profile.id,
        usage_profile_kind=profile.kind,
        config_version=agent.config_version,
        system_prompt=agent.system_prompt,
        memory_consent=agent.memory_consent and profile_memory_allowed,
        memories=memories,
        asr_provider=model.asr_provider,
        asr_model=model.asr_model,
        llm_provider=model.llm_provider,
        llm_model=model.llm_model,
        tts_provider=model.tts_provider,
        tts_model=model.tts_model,
        voice=voice.voice,
        llm_temperature=agent.llm_temperature,
        tts_speech_rate=agent.tts_speech_rate,
        tools=json.loads(agent.tools_json or "{}"),
        asr_cost_micros_per_minute=model.asr_cost_micros_per_minute,
        llm_input_cost_micros_per_million_tokens=(
            model.llm_input_cost_micros_per_million_tokens
        ),
        llm_output_cost_micros_per_million_tokens=(
            model.llm_output_cost_micros_per_million_tokens
        ),
        tts_cost_micros_per_10k_chars=model.tts_cost_micros_per_10k_chars,
    )
