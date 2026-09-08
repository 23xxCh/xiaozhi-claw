from fastapi import APIRouter, Depends, HTTPException, Request, status
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
from ..voice_routes import (
    CASCADE_FIELDS,
    compatible_voices,
    route_capabilities,
    validate_model_route,
    validate_route_admission,
)

router = APIRouter(prefix="/v1", tags=["presets"])


def _model_response(item: ModelPreset, voices: list[VoicePreset]) -> ModelPresetResponse:
    compatible = compatible_voices(item, voices)
    return ModelPresetResponse(
        id=item.id,
        display_name=item.display_name,
        description=item.description,
        is_default=item.is_default,
        route_kind=item.route_kind,
        capabilities=route_capabilities(item),
        compatible_voice_ids=[voice.id for voice in compatible],
        default_voice_preset_id=compatible[0].id if compatible else None,
    )


def _admin_model_response(item: ModelPreset, voices: list[VoicePreset]) -> AdminModelPresetResponse:
    return AdminModelPresetResponse(
        **_model_response(item, voices).model_dump(),
        realtime_provider=item.realtime_provider,
        realtime_model=item.realtime_model,
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
    voices = list(await session.scalars(select(VoicePreset).where(VoicePreset.enabled.is_(True))))
    return [_model_response(item, voices) for item in presets]


@router.get("/voice-presets", response_model=list[VoicePresetResponse])
async def list_voice_presets(
    model_preset_id: str | None = None,
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
    if model_preset_id is not None:
        model = await session.get(ModelPreset, model_preset_id)
        if model is None or not model.enabled:
            raise HTTPException(status_code=422, detail="model preset unavailable")
        presets = compatible_voices(model, presets)
    else:
        models = list(
            await session.scalars(select(ModelPreset).where(ModelPreset.enabled.is_(True)))
        )
        available_ids = {
            voice.id for model in models for voice in compatible_voices(model, presets)
        }
        presets = [voice for voice in presets if voice.id in available_ids]
    return [
        VoicePresetResponse(
            id=item.id,
            display_name=item.display_name,
            language=item.language,
            provider=item.provider,
            voice=item.voice,
            preview_url=item.preview_url,
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
    voices = list(await session.scalars(select(VoicePreset).where(VoicePreset.enabled.is_(True))))
    return [_admin_model_response(item, voices) for item in presets]


@router.patch("/admin/model-presets/{preset_id}", response_model=AdminModelPresetResponse)
async def update_model_preset(
    preset_id: str,
    payload: AdminModelPresetUpdateRequest,
    request: Request,
    staff: StaffUser = Depends(require_staff(StaffRole.SUPERADMIN, StaffRole.ENGINEERING)),
    session: AsyncSession = Depends(get_session),
) -> AdminModelPresetResponse:
    preset = await session.get(ModelPreset, preset_id)
    if preset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model preset not found")
    nullable_route_fields = {*CASCADE_FIELDS, "realtime_provider", "realtime_model"}
    changes = {
        key: value
        for key, value in payload.model_dump(exclude={"confirm"}, exclude_unset=True).items()
        if value is not None or key in nullable_route_fields
    }
    for key, value in changes.items():
        setattr(preset, key, value)
    try:
        validate_model_route(preset)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if preset.is_default and not preset.enabled:
        raise HTTPException(status_code=422, detail="default model preset must be enabled")
    if preset.enabled:
        try:
            validate_route_admission(preset, request.app.state.settings)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    voices = list(await session.scalars(select(VoicePreset).where(VoicePreset.enabled.is_(True))))
    if preset.enabled and not compatible_voices(preset, voices):
        raise HTTPException(
            status_code=422, detail="enabled model preset requires a compatible voice"
        )
    if changes.get("is_default") is True:
        await session.execute(
            update(ModelPreset).where(ModelPreset.id != preset.id).values(is_default=False)
        )
    add_audit_event(
        session,
        actor_type="staff",
        actor_id=staff.id,
        action="model-preset.updated",
        payload={"preset_id": preset.id, "fields": sorted(changes)},
    )
    await session.commit()
    return _admin_model_response(preset, voices)
