from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ConversationSession, ModelPreset, ProviderUsage


def _usage_cost_micros(usage: ProviderUsage, rates: dict[tuple[str, str, str], int]) -> int:
    rate = rates.get((usage.operation, usage.provider, usage.model), 0)
    if usage.operation == "asr":
        return round(rate * usage.input_units / 60_000)
    if usage.operation == "llm":
        input_rate = rates.get(("llm-input", usage.provider, usage.model), 0)
        output_rate = rates.get(("llm-output", usage.provider, usage.model), 0)
        return round(
            input_rate * usage.input_units / 1_000_000
            + output_rate * usage.output_units / 1_000_000
        )
    if usage.operation == "tts":
        return round(rate * usage.input_units / 10_000)
    return 0


async def backfill_unpriced_provider_usage(session: AsyncSession) -> int:
    presets = list(await session.scalars(select(ModelPreset)))
    rates: dict[tuple[str, str, str], int] = {}
    for preset in presets:
        rates[("asr", preset.asr_provider, preset.asr_model)] = (
            preset.asr_cost_micros_per_minute
        )
        rates[("llm-input", preset.llm_provider, preset.llm_model)] = (
            preset.llm_input_cost_micros_per_million_tokens
        )
        rates[("llm-output", preset.llm_provider, preset.llm_model)] = (
            preset.llm_output_cost_micros_per_million_tokens
        )
        rates[("tts", preset.tts_provider, preset.tts_model)] = (
            preset.tts_cost_micros_per_10k_chars
        )

    changed_session_ids: set[str] = set()
    updated = 0
    usages = list(
        await session.scalars(select(ProviderUsage).where(ProviderUsage.cost_micros == 0))
    )
    for usage in usages:
        cost = _usage_cost_micros(usage, rates)
        if cost <= 0:
            continue
        usage.cost_micros = cost
        updated += 1
        if usage.session_id:
            changed_session_ids.add(usage.session_id)

    if changed_session_ids:
        totals = (
            await session.execute(
                select(ProviderUsage.session_id, func.sum(ProviderUsage.cost_micros))
                .where(ProviderUsage.session_id.in_(changed_session_ids))
                .group_by(ProviderUsage.session_id)
            )
        ).all()
        for session_id, total in totals:
            conversation = await session.get(ConversationSession, session_id)
            if conversation is not None:
                conversation.provider_cost_micros = int(total or 0)
    return updated
