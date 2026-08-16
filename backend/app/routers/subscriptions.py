from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..dependencies import require_adult_user
from ..models import ModelPreset, ProviderUsage, UsageEvent, User
from ..quota import quota_for_user
from ..schemas import EntitlementResponse, UsageSummaryResponse

router = APIRouter(prefix="/v1/account", tags=["subscriptions"])


@router.get("/entitlement", response_model=EntitlementResponse)
async def entitlement(
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> EntitlementResponse:
    quota = await quota_for_user(session, user.id, request.app.state.settings)
    return EntitlementResponse(
        plan=quota.plan,
        monthly_turn_limit=quota.limit,
        used_turns=quota.used,
        remaining_turns=quota.remaining,
        expires_at=quota.expires_at,
    )


@router.get("/usage", response_model=UsageSummaryResponse)
async def usage(
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> UsageSummaryResponse:
    turns = await session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.user_id == user.id,
            UsageEvent.kind == "voice-turn",
        )
    )
    usages = list(
        await session.scalars(select(ProviderUsage).where(ProviderUsage.user_id == user.id))
    )
    pricing_configured = bool(
        await session.scalar(
            select(ModelPreset.id).where(
                (ModelPreset.asr_cost_micros_per_minute > 0)
                | (ModelPreset.llm_input_cost_micros_per_million_tokens > 0)
                | (ModelPreset.llm_output_cost_micros_per_million_tokens > 0)
                | (ModelPreset.tts_cost_micros_per_10k_chars > 0)
            )
        )
    )
    return UsageSummaryResponse(
        voice_turns=int(turns or 0),
        provider_cost_micros=sum(item.cost_micros for item in usages),
        pricing_configured=pricing_configured,
        asr_units=sum(item.input_units for item in usages if item.operation == "asr"),
        llm_input_units=sum(item.input_units for item in usages if item.operation == "llm"),
        llm_output_units=sum(item.output_units for item in usages if item.operation == "llm"),
        tts_units=sum(item.input_units for item in usages if item.operation == "tts"),
    )
