from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ReferralAttribution,
    ReferralTrafficQualification,
    ReferralTrafficRewardGrant,
)


logger = structlog.get_logger(__name__)


async def get_referral_attribution_by_referral_id(
    db: AsyncSession,
    referral_id: int,
) -> ReferralAttribution | None:
    result = await db.execute(select(ReferralAttribution).where(ReferralAttribution.referral_id == referral_id))
    return result.scalar_one_or_none()


async def create_referral_attribution(
    db: AsyncSession,
    referral_id: int,
    referrer_id: int,
    referral_code: str | None,
    mode: str,
    mode_captured_at: datetime | None = None,
    *,
    commit: bool = True,
) -> ReferralAttribution:
    attribution = ReferralAttribution(
        referral_id=referral_id,
        referrer_id=referrer_id,
        referral_code=referral_code,
        mode=mode,
        mode_captured_at=mode_captured_at or datetime.now(UTC),
    )
    db.add(attribution)

    try:
        if commit:
            await db.commit()
            await db.refresh(attribution)
        return attribution
    except IntegrityError:
        await db.rollback()
        existing = await get_referral_attribution_by_referral_id(db, referral_id)
        if existing:
            return existing
        raise


async def get_referral_traffic_qualification_by_referral_id(
    db: AsyncSession,
    referral_id: int,
) -> ReferralTrafficQualification | None:
    result = await db.execute(
        select(ReferralTrafficQualification).where(ReferralTrafficQualification.referral_id == referral_id)
    )
    return result.scalar_one_or_none()


async def create_referral_traffic_qualification(
    db: AsyncSession,
    *,
    referrer_id: int,
    referral_id: int,
    subscription_id: int | None,
    remnawave_uuid: str | None,
    traffic_used_gb: float,
    commit: bool = True,
) -> ReferralTrafficQualification | None:
    qualification = ReferralTrafficQualification(
        referrer_id=referrer_id,
        referral_id=referral_id,
        subscription_id=subscription_id,
        remnawave_uuid=remnawave_uuid,
        traffic_used_gb=traffic_used_gb,
        qualified_at=datetime.now(UTC),
    )
    db.add(qualification)

    try:
        if commit:
            await db.commit()
            await db.refresh(qualification)
        return qualification
    except IntegrityError:
        await db.rollback()
        return await get_referral_traffic_qualification_by_referral_id(db, referral_id)


async def count_referrer_traffic_qualifications(db: AsyncSession, referrer_id: int) -> int:
    result = await db.execute(
        select(func.count(ReferralTrafficQualification.id)).where(
            ReferralTrafficQualification.referrer_id == referrer_id
        )
    )
    return result.scalar() or 0


async def get_referral_traffic_reward_grant_by_referrer_id(
    db: AsyncSession,
    referrer_id: int,
) -> ReferralTrafficRewardGrant | None:
    result = await db.execute(
        select(ReferralTrafficRewardGrant).where(ReferralTrafficRewardGrant.referrer_id == referrer_id)
    )
    return result.scalar_one_or_none()


async def create_referral_traffic_reward_grant(
    db: AsyncSession,
    *,
    referrer_id: int,
    qualified_count_at_grant: int,
    reward_days: int,
    subscription_id: int | None,
    commit: bool = True,
) -> ReferralTrafficRewardGrant | None:
    grant = ReferralTrafficRewardGrant(
        referrer_id=referrer_id,
        qualified_count_at_grant=qualified_count_at_grant,
        reward_days=reward_days,
        subscription_id=subscription_id,
        granted_at=datetime.now(UTC),
    )
    db.add(grant)

    try:
        if commit:
            await db.commit()
            await db.refresh(grant)
        return grant
    except IntegrityError:
        await db.rollback()
        return await get_referral_traffic_reward_grant_by_referrer_id(db, referrer_id)
