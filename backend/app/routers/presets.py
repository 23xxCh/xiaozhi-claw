from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..catalog import ensure_catalog
from ..db import get_session
from ..dependencies import require_adult_user, require_staff
from ..models import ModelPreset, StaffRole, StaffUser, User, VoicePreset
from ..schemas import (
    AdminModelPresetResponse,
    AdminModelPresetUpdateRequest,
    ModelPresetResponse,
    VoicePresetResponse,
)

router = APIRouter(prefix="/v1", tags=["presets"])


def _admin_model_response(item: ModelPreset) -> AdminModelPresetResponse:
    return AdminModelPresetResponse(
        id=item.id,
        display_name=item.display_name,
        description=item.description,
        asr_provider=item.asr_provider,
        asr_model=item.asr_model,
        llm_provider=item.llm_provider,
        llm_model=item.llm_model,
        tts_provider=item.tts_provider,
        tts_model=item.tts_model,
        asr_cost_micros_per_minute=item.asr_cost_micros_per_minute,
        llm_input_cost_micros_per_million_tokens=(item.llm_input_cost_micros_per_million_tokens),
        llm_output_cost_micros_per_million_tokens=(item.llm_output_cost_micros_per_million_tokens),
        tts_cost_micros_per_10k_chars=item.tts_cost_micros_per_10k_chars,
        enabled=item.enabled,
        is_default=item.is_default,
    )


@router.get("/model-presets", response_model=list[ModelPresetResponse])
async def list_model_presets(
    _: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> list[ModelPresetResponse]:
    await ensure_catalog(session)
    await session.commit()
    presets = list(
        await session.scalars(
            select(ModelPreset)
            .where(ModelPreset.enabled.is_(True))
            .order_by(ModelPreset.is_default.desc(), ModelPreset.display_name)
        )
    )
    return [
        ModelPresetResponse(
            id=item.id,
            display_name=item.display_name,
            description=item.description,
            is_default=item.is_default,
        )
        for item in presets
    ]


@router.get("/voice-presets", response_model=list[VoicePresetResponse])
async def list_voice_presets(
    _: User = Depends(require_adult_user),
    session: AsyncSession = Depends(get_session),
) -> list[VoicePresetResponse]:
    await ensure_catalog(session)
    await session.commit()
    presets = list(
        await session.scalars(
            select(VoicePreset)
            .where(VoicePreset.enabled.is_(True))
            .order_by(VoicePreset.is_default.desc(), VoicePreset.display_name)
        )
    )
    return [
        VoicePresetResponse(
            id=item.id,
            display_name=item.display_name,
            language=item.language,
            voice=item.voice,
            is_default=item.is_default,
        )
        for item in presets
    ]


@router.get("/admin/model-presets", response_model=list[AdminModelPresetResponse])
async def admin_model_presets(
    _: StaffUser = Depends(require_staff(StaffRole.SUPERADMIN, StaffRole.ENGINEERING)),
    session: AsyncSession = Depends(get_session),
) -> list[AdminModelPresetResponse]:
    await ensure_catalog(session)
    await session.commit()
    presets = list(await session.scalars(select(ModelPreset).order_by(ModelPreset.display_name)))
    return [_admin_model_response(item) for item in presets]


@router.patch("/admin/model-presets/{preset_id}", response_model=AdminModelPresetResponse)
async def update_model_preset(
    preset_id: str,
    payload: AdminModelPresetUpdateRequest,
    staff: StaffUser = Depends(require_staff(StaffRole.SUPERADMIN, StaffRole.ENGINEERING)),
    session: AsyncSession = Depends(get_session),
) -> AdminModelPresetResponse:
    preset = await session.get(ModelPreset, preset_id)
    if preset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model preset not found")
    changes = payload.model_dump(exclude={"confirm"}, exclude_none=True)
    if changes.get("is_default") is True:
        await session.execute(update(ModelPreset).values(is_default=False))
    for key, value in changes.items():
        setattr(preset, key, value)
    add_audit_event(
        session,
        actor_type="staff",
        actor_id=staff.id,
        action="model-preset.updated",
        payload={"preset_id": preset.id, "fields": sorted(changes)},
    )
    await session.commit()
    return _admin_model_response(preset)
