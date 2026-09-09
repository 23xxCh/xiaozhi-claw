import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..catalog import ensure_catalog, ensure_default_agent, resolve_agent_presets
from ..db import get_session
from ..dependencies import require_adult_user
from ..memory_lifecycle import invalidate_memory
from ..models import (
    Agent,
    AgentMemory,
    Device,
    EncryptedSessionSummary,
    ModelPreset,
    UsageProfile,
    User,
    VoicePreset,
)
from ..schemas import AgentCreateRequest, AgentResponse, AgentUpdateRequest
from ..usage_profiles import ensure_adult_profile
from ..voice_routes import route_capabilities

router = APIRouter(prefix="/v1/agents", tags=["agents"])


async def owned_agent(session: AsyncSession, user: User, agent_id: str) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    return agent


async def _response(
    session: AsyncSession,
    agent: Agent,
    *,
    device_count: int | None = None,
) -> AgentResponse:
    if device_count is None:
        device_count = int(
            await session.scalar(
                select(func.count()).select_from(Device).where(Device.active_agent_id == agent.id)
            )
            or 0
        )
    return AgentResponse(
        id=agent.id,
        usage_profile_id=agent.usage_profile_id,
        name=agent.name,
        avatar_url=agent.avatar_url,
        system_prompt=agent.system_prompt,
        model_preset_id=agent.model_preset_id,
        voice_preset_id=agent.voice_preset_id,
        memory_consent=agent.memory_consent,
        tools=json.loads(agent.tools_json or "{}"),
        llm_temperature=agent.llm_temperature,
        tts_speech_rate=agent.tts_speech_rate,
        config_version=agent.config_version,
        device_count=device_count,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


async def _validate_presets(
    session: AsyncSession, model_preset_id: str | None, voice_preset_id: str | None
) -> tuple[ModelPreset, VoicePreset]:
    try:
        return await resolve_agent_presets(session, model_preset_id, voice_preset_id)
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail=str(error)
        ) from error


def _validate_parameters(
    model: ModelPreset, payload: AgentCreateRequest | AgentUpdateRequest, agent: Agent | None = None
) -> None:
    capabilities = route_capabilities(model)
    if not capabilities.system_prompt and "system_prompt" in payload.model_fields_set:
        if agent is None or payload.system_prompt != agent.system_prompt:
            raise HTTPException(status_code=422, detail="selected route uses application persona")
    for field, default in (("llm_temperature", 0.6), ("tts_speech_rate", 1.0)):
        value = getattr(payload, field)
        previous = getattr(agent, field) if agent else default
        # Legacy clients may send the entire form. An unchanged inactive value
        # stays stored for switching back, but never becomes an active parameter.
        if value is not None and value != previous and not getattr(capabilities, field):
            raise HTTPException(status_code=422, detail=f"selected route does not support {field}")
    if isinstance(payload, AgentUpdateRequest) and payload.tools is not None:
        previous_tools = json.loads(agent.tools_json or "{}") if agent else {}
        if not capabilities.tools and payload.tools != previous_tools:
            raise HTTPException(status_code=422, detail="selected route does not support tools")


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> list[AgentResponse]:
    await ensure_default_agent(session, user)
    await session.commit()
    agents = list(
        await session.scalars(
            select(Agent).where(Agent.owner_user_id == user.id).order_by(Agent.created_at)
        )
    )
    if not agents:
        return []
    device_counts = {
        agent_id: int(device_count)
        for agent_id, device_count in (
            await session.execute(
                select(Device.active_agent_id, func.count())
                .where(Device.active_agent_id.in_([agent.id for agent in agents]))
                .group_by(Device.active_agent_id)
            )
        ).all()
    }
    return [
        await _response(session, agent, device_count=device_counts.get(agent.id, 0))
        for agent in agents
    ]


@router.post("", response_model=AgentResponse)
async def create_agent(
    payload: AgentCreateRequest,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> AgentResponse:
    await ensure_catalog(session)
    model, voice = await _validate_presets(
        session, payload.model_preset_id, payload.voice_preset_id
    )
    _validate_parameters(model, payload)
    if payload.usage_profile_id is None:
        profile_id = (await ensure_adult_profile(session, user)).id
    else:
        profile = await session.get(UsageProfile, payload.usage_profile_id)
        if profile is None or profile.owner_user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="profile not found")
        profile_id = profile.id
    agent = Agent(
        owner_user_id=user.id,
        usage_profile_id=profile_id,
        name=payload.name,
        avatar_url=str(payload.avatar_url) if payload.avatar_url else None,
        system_prompt=payload.system_prompt,
        model_preset_id=model.id,
        voice_preset_id=voice.id,
        llm_temperature=payload.llm_temperature,
        tts_speech_rate=payload.tts_speech_rate,
    )
    session.add(agent)
    await session.flush()
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="agent.created",
        payload={"agent_id": agent.id},
    )
    await session.commit()
    return await _response(session, agent)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> AgentResponse:
    return await _response(session, await owned_agent(session, user, agent_id))


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    payload: AgentUpdateRequest,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> AgentResponse:
    agent = await owned_agent(session, user, agent_id)
    model_id = payload.model_preset_id or agent.model_preset_id
    voice_id = payload.voice_preset_id or agent.voice_preset_id
    model, _ = await _validate_presets(session, model_id, voice_id)
    _validate_parameters(model, payload, agent)
    if payload.name is not None:
        agent.name = payload.name
    if payload.avatar_url is not None:
        agent.avatar_url = str(payload.avatar_url)
    if payload.system_prompt is not None:
        agent.system_prompt = payload.system_prompt
    if payload.model_preset_id is not None:
        agent.model_preset_id = payload.model_preset_id
    if payload.voice_preset_id is not None:
        agent.voice_preset_id = payload.voice_preset_id
    if payload.memory_consent is not None:
        if payload.memory_consent != agent.memory_consent:
            await invalidate_memory(session, user.id, agent.id)
        agent.memory_consent = payload.memory_consent
        if not payload.memory_consent:
            await session.execute(delete(AgentMemory).where(AgentMemory.agent_id == agent.id))
            await session.execute(
                delete(EncryptedSessionSummary).where(EncryptedSessionSummary.agent_id == agent.id)
            )
    if payload.tools is not None:
        agent.tools_json = json.dumps(payload.tools, ensure_ascii=False, sort_keys=True)
    if payload.llm_temperature is not None:
        agent.llm_temperature = payload.llm_temperature
    if payload.tts_speech_rate is not None:
        agent.tts_speech_rate = payload.tts_speech_rate
    agent.config_version += 1
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="agent.updated",
        payload={"agent_id": agent.id, "config_version": agent.config_version},
    )
    await session.commit()
    return await _response(session, agent)


@router.put("/{agent_id}/devices/{device_id}", response_model=AgentResponse)
async def assign_device(
    agent_id: str,
    device_id: str,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> AgentResponse:
    agent = await owned_agent(session, user, agent_id)
    device = await session.get(Device, device_id)
    if device is None or device.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    device.active_agent_id = agent.id
    device.active_profile_id = agent.usage_profile_id
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="device.agent-assigned",
        payload={"device_id": device.id, "agent_id": agent.id},
    )
    await session.commit()
    return await _response(session, agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: str,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    agent = await owned_agent(session, user, agent_id)
    assigned = await session.scalar(
        select(func.count()).select_from(Device).where(Device.active_agent_id == agent.id)
    )
    if assigned:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="agent still has devices")
    await session.delete(agent)
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="agent.deleted",
        payload={"agent_id": agent.id},
    )
    await session.commit()
