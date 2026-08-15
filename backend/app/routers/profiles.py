from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import require_adult_user
from ..models import Agent, Device, UsageProfile, UsageProfileKind, User
from ..schemas import (
    ActiveProfileRequest,
    DeviceDetailResponse,
    GuardianControlsRequest,
    UsageProfileCreateRequest,
    UsageProfileResponse,
)
from ..usage_profiles import (
    clock_to_minute,
    ensure_adult_profile,
    minute_to_clock,
    new_youth_profile,
)
from .devices import _device_detail_response

router = APIRouter(prefix="/v1", tags=["profiles"])


def _response(profile: UsageProfile) -> UsageProfileResponse:
    return UsageProfileResponse(
        id=profile.id,
        kind=cast(Literal["adult", "youth"], profile.kind),
        display_name=profile.display_name,
        age_band=cast(Literal["12_13", "14_17"] | None, profile.age_band),
        guardian_consent_version=profile.guardian_consent_version,
        guardian_consent_at=profile.guardian_consent_at,
        memory_consent=profile.memory_consent,
        quiet_start=minute_to_clock(profile.quiet_start_minute),
        quiet_end=minute_to_clock(profile.quiet_end_minute),
        daily_limit_minutes=profile.daily_limit_minutes,
        continuous_reminder_minutes=profile.continuous_reminder_minutes,
    )


async def _owned_profile(session: AsyncSession, user: User, profile_id: str) -> UsageProfile:
    profile = await session.get(UsageProfile, profile_id)
    if profile is None or profile.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="profile not found")
    return profile


@router.get("/profiles", response_model=list[UsageProfileResponse])
async def list_profiles(
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> list[UsageProfileResponse]:
    await ensure_adult_profile(session, user)
    await session.commit()
    profiles = list(
        await session.scalars(
            select(UsageProfile)
            .where(UsageProfile.owner_user_id == user.id)
            .order_by(UsageProfile.kind, UsageProfile.created_at)
        )
    )
    return [_response(profile) for profile in profiles]


@router.post("/profiles", response_model=UsageProfileResponse)
async def create_youth_profile(
    payload: UsageProfileCreateRequest,
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> UsageProfileResponse:
    settings = request.app.state.settings
    if not settings.family_mode_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="family mode is not available"
        )
    whitelist = settings.family_mode_allowed_openids
    if whitelist and user.wechat_openid not in whitelist:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="family mode is not enabled for account"
        )
    profile = new_youth_profile(
        user=user,
        display_name=payload.display_name,
        age_band=payload.age_band,
    )
    session.add(profile)
    await session.flush()
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="profile.youth-created",
        payload={"profile_id": profile.id, "age_band": profile.age_band},
    )
    await session.commit()
    return _response(profile)


@router.patch("/profiles/{profile_id}/guardian-controls", response_model=UsageProfileResponse)
async def update_guardian_controls(
    profile_id: str,
    payload: GuardianControlsRequest,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> UsageProfileResponse:
    profile = await _owned_profile(session, user, profile_id)
    if profile.kind != UsageProfileKind.YOUTH.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="guardian controls apply to youth profiles only",
        )
    changes = payload.model_dump(exclude_none=True)
    if payload.memory_consent is not None:
        profile.memory_consent = payload.memory_consent
    if payload.quiet_start is not None:
        profile.quiet_start_minute = clock_to_minute(payload.quiet_start)
    if payload.quiet_end is not None:
        profile.quiet_end_minute = clock_to_minute(payload.quiet_end)
    if payload.daily_limit_minutes is not None:
        profile.daily_limit_minutes = payload.daily_limit_minutes
    if payload.continuous_reminder_minutes is not None:
        profile.continuous_reminder_minutes = payload.continuous_reminder_minutes
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="profile.guardian-controls-updated",
        payload={"profile_id": profile.id, "fields": sorted(changes)},
    )
    await session.commit()
    return _response(profile)


@router.patch("/devices/{device_id}/active-profile", response_model=DeviceDetailResponse)
async def switch_active_profile(
    device_id: str,
    payload: ActiveProfileRequest,
    request: Request,
    user: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceDetailResponse:
    device = await session.get(Device, device_id)
    if device is None or device.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    profile = await _owned_profile(session, user, payload.profile_id)
    if (
        profile.kind == UsageProfileKind.YOUTH.value
        and not request.app.state.settings.family_mode_enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="family mode is not available"
        )
    if payload.agent_id:
        agent = await session.get(Agent, payload.agent_id)
        if agent is None or agent.owner_user_id != user.id or agent.usage_profile_id != profile.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    else:
        agent = await session.scalar(
            select(Agent).where(Agent.usage_profile_id == profile.id).order_by(Agent.created_at)
        )
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="profile needs an assistant"
        )
    device.active_profile_id = profile.id
    device.active_agent_id = agent.id
    add_audit_event(
        session,
        actor_type="user",
        actor_id=user.id,
        action="device.profile-switched",
        payload={"device_id": device.id, "profile_id": profile.id, "agent_id": agent.id},
    )
    await session.commit()
    return await _device_detail_response(
        session, device, request.app.state.settings.device_offline_after_seconds
    )
