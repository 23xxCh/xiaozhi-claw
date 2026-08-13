from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ConversationSession, ProviderUsage, UsageProfile, UsageProfileKind


@dataclass(frozen=True)
class ProfilePolicyDecision:
    allowed: bool
    code: str | None = None
    message: str | None = None
    continuous_reminder_due: bool = False


def _inside_quiet_hours(current: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


async def evaluate_profile_policy(
    session: AsyncSession,
    profile: UsageProfile,
    *,
    family_mode_enabled: bool,
    now: datetime | None = None,
    conversation_started_at: datetime | None = None,
) -> ProfilePolicyDecision:
    if profile.kind == UsageProfileKind.ADULT.value:
        return ProfilePolicyDecision(allowed=True)
    if not family_mode_enabled:
        return ProfilePolicyDecision(
            allowed=False,
            code="family-mode-unavailable",
            message="家庭模式暂未开放，请切换到成人档案。",
        )

    current = now or datetime.now(UTC)
    local = current.astimezone(ZoneInfo("Asia/Hong_Kong"))
    current_minute = local.hour * 60 + local.minute
    if _inside_quiet_hours(current_minute, profile.quiet_start_minute, profile.quiet_end_minute):
        return ProfilePolicyDecision(
            allowed=False,
            code="quiet-hours",
            message="现在是休息时段，明天再聊吧。",
        )

    local_start = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo).astimezone(UTC)
    used_ms = await session.scalar(
        select(func.coalesce(func.sum(ProviderUsage.input_units), 0))
        .join(ConversationSession, ProviderUsage.session_id == ConversationSession.id)
        .where(
            ConversationSession.usage_profile_id == profile.id,
            ProviderUsage.operation == "asr",
            ProviderUsage.created_at >= local_start,
        )
    )
    if int(used_ms or 0) >= profile.daily_limit_minutes * 60_000:
        return ProfilePolicyDecision(
            allowed=False,
            code="daily-limit",
            message="今天的使用时间已经到了，请明天再来。",
        )

    reminder_due = False
    if conversation_started_at is not None:
        started = conversation_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        reminder_due = (
            current - started
        ).total_seconds() >= profile.continuous_reminder_minutes * 60
    return ProfilePolicyDecision(allowed=True, continuous_reminder_due=reminder_due)
