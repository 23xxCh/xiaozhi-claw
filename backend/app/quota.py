from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import Entitlement, UsageEvent


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    plan: str
    limit: int
    used: int
    expires_at: datetime | None

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


async def quota_for_user(
    session: AsyncSession, user_id: str, settings: Settings, now: datetime | None = None
) -> QuotaSnapshot:
    now = now or datetime.now(UTC)
    entitlement = await session.scalar(
        select(Entitlement)
        .where(Entitlement.user_id == user_id, Entitlement.expires_at > now)
        .order_by(Entitlement.expires_at.desc())
    )
    if entitlement:
        plan = entitlement.plan
        limit = entitlement.monthly_turn_limit
        expires_at = _as_utc(entitlement.expires_at)
    else:
        plan = "free"
        limit = settings.free_monthly_turns
        expires_at = None

    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    used = await session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.kind == "voice-turn",
            UsageEvent.created_at >= month_start,
        )
    )
    return QuotaSnapshot(plan, limit, int(used or 0), expires_at)
