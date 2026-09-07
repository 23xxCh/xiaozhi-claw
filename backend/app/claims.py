import sqlite3
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import Claim, Device
from .security import hash_secret, verify_secret


async def begin_claim_issuance(session: AsyncSession) -> None:
    """Set isolation for this bootstrap only, before any database operation."""
    if session.get_bind().dialect.name != "mysql":
        return
    if session.in_transaction():
        raise RuntimeError("claim issuance isolation must be set before database use")
    # The Device row serializes each device; avoid RR gap locks between new devices.
    # SQLAlchemy restores the default isolation when this connection returns to its pool.
    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})


async def lock_claim_device(session: AsyncSession, device: Device) -> None:
    if session.get_bind().dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE; serialize issuance/consumption in the database.
        await session.execute(update(Device).where(Device.id == device.id).values(id=Device.id))
    await session.refresh(device, with_for_update=True)


def _claim_code(claim_id: str, device_id: str, pepper: str) -> str:
    digest = hash_secret(f"device-claim:v1:{device_id}:{claim_id}", pepper)
    return f"{int(digest, 16) % 1_000_000:06d}"


def _is_claim_key_collision(error: IntegrityError) -> bool:
    original = error.orig
    sqlite_code = getattr(original, "sqlite_errorcode", None)
    if sqlite_code is not None:
        return sqlite_code in {
            sqlite3.SQLITE_CONSTRAINT_UNIQUE,
            sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
        } and str(original) in {
            "UNIQUE constraint failed: claims.code_hash",
            "UNIQUE constraint failed: claims.id",
        }
    # This savepoint only inserts the new Claim; a duplicate PK is also safe to retry.
    return bool(original.args) and original.args[0] == 1062


async def issue_claim(
    session: AsyncSession, device: Device, settings: Settings, now: datetime
) -> tuple[str, Claim]:
    """The caller holds the device lock until commit; only the existing hash is stored."""
    active = await session.scalars(
        select(Claim)
        .where(
            Claim.device_id == device.id,
            Claim.consumed_at.is_(None),
            Claim.expires_at > now,
        )
        .order_by(Claim.created_at.desc())
        .with_for_update()
    )
    for claim in active:
        code = _claim_code(claim.id, device.id, settings.device_credential_pepper)
        if verify_secret(code, claim.code_hash, settings.device_credential_pepper):
            return code, claim

    # Legacy random codes remain valid until their original expiry. Add one reusable code.
    for _ in range(8):
        claim_id = str(uuid.uuid4())
        code = _claim_code(claim_id, device.id, settings.device_credential_pepper)
        claim = Claim(
            id=claim_id,
            code_hash=hash_secret(code, settings.device_credential_pepper),
            device_id=device.id,
            created_at=now,
            expires_at=now + timedelta(seconds=settings.claim_ttl_seconds),
        )
        try:
            async with session.begin_nested():
                session.add(claim)
                await session.flush()
        except IntegrityError as exc:
            # Do not lock the collision row or consult an old MySQL RR snapshot.
            # The unique key keeps its original device; retry with a fresh UUID/code.
            if not _is_claim_key_collision(exc):
                raise
        else:
            return code, claim
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="claim code unavailable"
    )


async def consume_device_claims(session: AsyncSession, device_id: str, now: datetime) -> None:
    await session.execute(
        update(Claim)
        .where(Claim.device_id == device_id, Claim.consumed_at.is_(None))
        .values(consumed_at=now)
    )
