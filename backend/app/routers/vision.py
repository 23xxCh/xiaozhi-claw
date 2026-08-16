from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..dependencies import authenticate_device
from ..schemas import VisionCapabilityResponse

router = APIRouter(prefix="/v1/device/vision", tags=["vision"])


@router.get("/capability", response_model=VisionCapabilityResponse)
async def vision_capability(
    request: Request,
    device_id: str = Header(alias="Device-Id"),
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> VisionCapabilityResponse:
    await authenticate_device(
        request,
        session,
        device_id,
        authorization.removeprefix("Bearer "),
    )
    return VisionCapabilityResponse()
