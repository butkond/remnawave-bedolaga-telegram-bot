from datetime import UTC, datetime
from inspect import isawaitable

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ReferralAttribution,
    ReferralRewardMode,
    ReferralTrafficQualification,
    ReferralTrafficRewardGrant,
)


logger = structlog.get_logger(__name__)


async def _add(db: AsyncSession, instance) -> None:
    result = db.add(instance)
    if isawaitable(result):
        await result


async def get_referral_attribution(db: AsyncSession, referral_id: int) -> ReferralAttribution | None:
    result = await db.execute(select(ReferralAttribution).where(ReferralAttribution.referral_id == referral_id))
    return result.scalar_one_or_none()


async def ensure_referral_attribution(
    db: AsyncSession,
    *,
    referral_id: int,
    referrer_id: int,
    referral_code: str | None = None,
    mode: ReferralRewardMode | str = ReferralRewardMode.BALANCE_COMMISSION,
    commit: bool = True,
) -> ReferralAttribution | None:
    existing = await get_referral_attribution(db, referral_id)
    if existing:
        return existing

    mode_value = mode.value if isinstance(mode, ReferralRewardMode) else str(mode)
    attribution = ReferralAttribution(
        referral_id=referral_id,
        referrer_id=referrer_id,
        referral_code=referral_code,
        mode=mode_value,
        mode_captured_at=datetime.now(UTC),
    )
    await _add(db, attribution)

    try:
        if commit:
            await db.commit()
            await db.refresh(attribution)
        return attribution
    except IntegrityError:
        await db.rollback()
        logger.info('Referral attribution already exists, treating as duplicate', referral_id=referral_id)
        return await get_referral_attribution(db, referral_id)


async def create_qualification(
    db: AsyncSession,
    *,
    referrer_id: int,
    referral_id: int,
    subscription_id: int | None,
    remnawave_uuid: str,
    source_event: str = 'user.first_connected',
) -> ReferralTrafficQualification | None:
    qualification = ReferralTrafficQualification(
        referrer_id=referrer_id,
        referral_id=referral_id,
        subscription_id=subscription_id,
        remnawave_uuid=remnawave_uuid,
        source_event=source_event,
        qualified_at=datetime.now(UTC),
    )
    await _add(db, qualification)
    try:
        await db.commit()
        await db.refresh(qualification)
        return qualification
    except IntegrityError:
        await db.rollback()
        logger.info(
            'Referral traffic qualification already exists, skipping duplicate',
            referral_id=referral_id,
        )
        return None


async def count_qualifications(db: AsyncSession, *, referrer_id: int) -> int:
    result = await db.execute(
        select(func.count(ReferralTrafficQualification.id)).where(
            ReferralTrafficQualification.referrer_id == referrer_id,
        )
    )
    return int(result.scalar() or 0)


async def get_reward_grant(
    db: AsyncSession,
    *,
    referrer_id: int,
    reward_cycle: int,
) -> ReferralTrafficRewardGrant | None:
    result = await db.execute(
        select(ReferralTrafficRewardGrant).where(
            ReferralTrafficRewardGrant.referrer_id == referrer_id,
            ReferralTrafficRewardGrant.reward_cycle == reward_cycle,
        )
    )
    return result.scalar_one_or_none()


async def get_last_reward_grant(
    db: AsyncSession,
    *,
    referrer_id: int,
) -> ReferralTrafficRewardGrant | None:
    result = await db.execute(
        select(ReferralTrafficRewardGrant)
        .where(ReferralTrafficRewardGrant.referrer_id == referrer_id)
        .order_by(ReferralTrafficRewardGrant.reward_cycle.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_reward_grant(
    db: AsyncSession,
    *,
    referrer_id: int,
    reward_cycle: int,
    qualified_count_at_grant: int,
    reward_days: int,
    subscription_id: int | None,
) -> ReferralTrafficRewardGrant | None:
    grant = ReferralTrafficRewardGrant(
        referrer_id=referrer_id,
        reward_cycle=reward_cycle,
        qualified_count_at_grant=qualified_count_at_grant,
        reward_days=reward_days,
        subscription_id=subscription_id,
        granted_at=datetime.now(UTC),
    )
    await _add(db, grant)
    try:
        await db.commit()
        await db.refresh(grant)
        return grant
    except IntegrityError:
        await db.rollback()
        logger.info(
            'Referral traffic reward grant already exists, skipping duplicate',
            referrer_id=referrer_id,
            reward_cycle=reward_cycle,
        )
        return None
