from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.audit import add_audit_event
from backend.app.models import ConversationSession, ProviderUsage, UsageEvent

from .snapshot import AgentSnapshot


async def record_turn(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    conversation_id: str,
    user_id: str,
    device_id: str,
    snapshot: AgentSnapshot,
    audio_duration_ms: int,
    transcript: str,
    reply: str,
    asr_latency_ms: int,
    llm_latency_ms: int,
    tts_latency_ms: int,
    first_audio_latency_ms: int | None,
    safety_category: str | None,
    fallback_operations: set[str],
) -> None:
    estimated_input_tokens = max(1, len(transcript) // 4)
    estimated_output_tokens = max(1, len(reply) // 4)
    asr_cost = round(snapshot.asr_cost_micros_per_minute * audio_duration_ms / 60_000)
    llm_cost = round(
        snapshot.llm_input_cost_micros_per_million_tokens * estimated_input_tokens / 1_000_000
        + snapshot.llm_output_cost_micros_per_million_tokens
        * estimated_output_tokens
        / 1_000_000
    )
    tts_cost = round(snapshot.tts_cost_micros_per_10k_chars * len(reply) / 10_000)
    total_cost = asr_cost + llm_cost + tts_cost
    async with session_factory() as session:
        conversation = await session.get(ConversationSession, conversation_id)
        if conversation is None:
            return
        conversation.turn_count += 1
        conversation.provider_cost_micros += total_cost
        if conversation.first_audio_latency_ms is None and first_audio_latency_ms is not None:
            conversation.first_audio_latency_ms = first_audio_latency_ms
        session.add(
            UsageEvent(
                user_id=user_id,
                device_id=device_id,
                kind="voice-turn",
                quantity=1,
                provider_cost_micros=total_cost,
            )
        )
        session.add_all(
            [
                ProviderUsage(
                    session_id=conversation_id,
                    user_id=user_id,
                    device_id=device_id,
                    provider=snapshot.asr_provider,
                    model=snapshot.asr_model,
                    operation="asr",
                    input_units=audio_duration_ms,
                    output_units=len(transcript),
                    latency_ms=asr_latency_ms,
                    cost_micros=asr_cost,
                    error_code=("fallback-batch" if "asr" in fallback_operations else None),
                ),
                ProviderUsage(
                    session_id=conversation_id,
                    user_id=user_id,
                    device_id=device_id,
                    provider=snapshot.llm_provider,
                    model=snapshot.llm_model,
                    operation="llm",
                    input_units=estimated_input_tokens,
                    output_units=estimated_output_tokens,
                    latency_ms=llm_latency_ms,
                    cost_micros=llm_cost,
                    error_code=("fallback-batch" if "llm" in fallback_operations else None),
                ),
                ProviderUsage(
                    session_id=conversation_id,
                    user_id=user_id,
                    device_id=device_id,
                    provider=snapshot.tts_provider,
                    model=snapshot.tts_model,
                    operation="tts",
                    input_units=len(reply),
                    latency_ms=tts_latency_ms,
                    cost_micros=tts_cost,
                    error_code=("fallback-batch" if "tts" in fallback_operations else None),
                ),
            ]
        )
        add_audit_event(
            session,
            actor_type="device",
            actor_id=device_id,
            action="voice.turn-completed",
            payload={
                "safety_category": safety_category,
                "agent_id": snapshot.agent_id,
                "config_version": snapshot.config_version,
                "fallback_operations": sorted(fallback_operations),
            },
        )
        await session.commit()
