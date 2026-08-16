from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UsageProfile, UsageProfileKind, User

GUARDIAN_CONSENT_VERSION = "2026-08-13-preview"


async def ensure_adult_profile(session: AsyncSession, user: User) -> UsageProfile:
    profile = await session.scalar(
        select(UsageProfile).where(
            UsageProfile.owner_user_id == user.id,
            UsageProfile.kind == UsageProfileKind.ADULT.value,
        )
    )
    if profile is None:
        profile = UsageProfile(
            owner_user_id=user.id,
            kind=UsageProfileKind.ADULT.value,
            display_name=user.display_name or "本人",
        )
        session.add(profile)
        await session.flush()
    return profile


async def ensure_all_adult_profiles(session: AsyncSession) -> None:
    users = list(await session.scalars(select(User).where(User.adult_confirmed.is_(True))))
    for user in users:
        await ensure_adult_profile(session, user)


def new_youth_profile(*, user: User, display_name: str, age_band: str) -> UsageProfile:
    return UsageProfile(
        owner_user_id=user.id,
        kind=UsageProfileKind.YOUTH.value,
        display_name=display_name,
        age_band=age_band,
        guardian_consent_version=GUARDIAN_CONSENT_VERSION,
        guardian_consent_at=datetime.now(UTC),
        memory_consent=False,
        quiet_start_minute=22 * 60,
        quiet_end_minute=7 * 60,
        daily_limit_minutes=90,
        continuous_reminder_minutes=30,
    )


def minute_to_clock(value: int) -> str:
    hours, minutes = divmod(value, 60)
    return f"{hours:02d}:{minutes:02d}"


def clock_to_minute(value: str) -> int:
    hours, minutes = value.split(":", maxsplit=1)
    return int(hours) * 60 + int(minutes)
