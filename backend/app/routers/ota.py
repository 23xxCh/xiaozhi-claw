import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..db import get_session
from ..dependencies import authenticate_device, require_admin
from ..models import FirmwareRelease
from ..schemas import FirmwareReleaseRequest, FirmwareReleaseResponse

router = APIRouter(prefix="/v1/ota", tags=["ota"])


def _signed_payload(payload: FirmwareReleaseRequest) -> bytes:
    return "|".join(
        [payload.board_type, payload.version, str(payload.artifact_url), payload.sha256]
    ).encode()


def _verify_release_signature(payload: FirmwareReleaseRequest, public_key: str) -> None:
    if not public_key:
        return
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
        key.verify(base64.b64decode(payload.signature), _signed_payload(payload))
    except (ValueError, InvalidSignature) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid firmware signature",
        ) from exc


def _version_tuple(version: str) -> tuple[int, int, int]:
    base = version.split("-", 1)[0].split("+", 1)[0]
    return tuple(int(part) for part in base.split("."))  # type: ignore[return-value]


@router.post(
    "/releases",
    response_model=FirmwareReleaseResponse,
    dependencies=[Depends(require_admin)],
)
async def register_release(
    payload: FirmwareReleaseRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> FirmwareReleaseResponse:
    _verify_release_signature(payload, request.app.state.settings.ota_signing_public_key)
    existing = await session.scalar(
        select(FirmwareRelease).where(
            FirmwareRelease.board_type == payload.board_type,
            FirmwareRelease.version == payload.version,
        )
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="release already exists")
    release = FirmwareRelease(
        board_type=payload.board_type,
        version=payload.version,
        artifact_url=str(payload.artifact_url),
        sha256=payload.sha256,
        signature=payload.signature,
        rollout_percent=payload.rollout_percent,
        mandatory=payload.mandatory,
        active=payload.active,
    )
    session.add(release)
    add_audit_event(
        session,
        actor_type="admin",
        actor_id="release-api",
        action="ota.release-registered",
        payload={
            "board_type": release.board_type,
            "version": release.version,
            "rollout_percent": release.rollout_percent,
        },
    )
    await session.commit()
    return FirmwareReleaseResponse(
        board_type=release.board_type,
        version=release.version,
        artifact_url=release.artifact_url,
        sha256=release.sha256,
        signature=release.signature,
        rollout_percent=release.rollout_percent,
        mandatory=release.mandatory,
    )


@router.get("/check", response_model=FirmwareReleaseResponse | None)
async def check_release(
    request: Request,
    response: Response,
    current_version: str,
    device_id: str = Header(alias="Device-Id"),
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> FirmwareReleaseResponse | None:
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing device secret"
        )
    device = await authenticate_device(
        request, session, device_id, authorization.removeprefix("Bearer ")
    )
    releases = list(
        await session.scalars(
            select(FirmwareRelease)
            .where(
                FirmwareRelease.board_type == device.board_type,
                FirmwareRelease.active.is_(True),
            )
            .order_by(FirmwareRelease.created_at.desc())
        )
    )
    selected = next(
        (
            release
            for release in releases
            if _version_tuple(release.version) > _version_tuple(current_version)
        ),
        None,
    )
    if selected is None:
        response.status_code = status.HTTP_204_NO_CONTENT
        return None

    bucket = int(hashlib.sha256(device.serial_number.encode()).hexdigest()[:8], 16) % 100
    if not selected.mandatory and bucket >= selected.rollout_percent:
        response.status_code = status.HTTP_204_NO_CONTENT
        return None
    return FirmwareReleaseResponse(
        board_type=selected.board_type,
        version=selected.version,
        artifact_url=selected.artifact_url,
        sha256=selected.sha256,
        signature=selected.signature,
        rollout_percent=selected.rollout_percent,
        mandatory=selected.mandatory,
    )
