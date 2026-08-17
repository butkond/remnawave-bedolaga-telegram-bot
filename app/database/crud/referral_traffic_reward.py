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


async def get_traffic_reward_summary(db: AsyncSession, *, referrer_id: int) -> dict[str, int]:
    qualified_count_result = await db.execute(
        select(func.count(ReferralTrafficQualification.id)).where(
            ReferralTrafficQualification.referrer_id == referrer_id,
        )
    )
    qualified_count = int(qualified_count_result.scalar() or 0)

    grants_result = await db.execute(
        select(
            func.coalesce(func.max(ReferralTrafficRewardGrant.qualified_count_at_grant), 0).label(
                'rewarded_qualified_count'
            ),
            func.coalesce(func.sum(ReferralTrafficRewardGrant.reward_days), 0).label('reward_days_earned'),
            func.count(ReferralTrafficRewardGrant.id).label('reward_grants_count'),
        ).where(ReferralTrafficRewardGrant.referrer_id == referrer_id)
    )
    grants_row = grants_result.one()
    rewarded_qualified_count = int(grants_row.rewarded_qualified_count or 0)
    reward_days_earned = int(grants_row.reward_days_earned or 0)
    reward_grants_count = int(grants_row.reward_grants_count or 0)

    return {
        'qualified_referrals_count': qualified_count,
        'rewarded_qualified_count': rewarded_qualified_count,
        'unrewarded_qualified_count': max(0, qualified_count - rewarded_qualified_count),
        'traffic_reward_days_earned': reward_days_earned,
        'traffic_reward_grants_count': reward_grants_count,
    }


async def get_traffic_reward_days_by_referral(
    db: AsyncSession,
    *,
    referrer_id: int,
    referral_ids: list[int] | None = None,
) -> dict[int, int]:
    grants_result = await db.execute(
        select(
            ReferralTrafficRewardGrant.qualified_count_at_grant,
            func.coalesce(func.sum(ReferralTrafficRewardGrant.reward_days), 0).label('reward_days'),
        )
        .where(ReferralTrafficRewardGrant.referrer_id == referrer_id)
        .group_by(ReferralTrafficRewardGrant.qualified_count_at_grant)
        .order_by(ReferralTrafficRewardGrant.qualified_count_at_grant.asc())
    )
    reward_days_by_qualification_number: dict[int, int] = {}
    previous_qualified_count = 0
    for row in grants_result.all():
        qualified_count_at_grant = int(row.qualified_count_at_grant)
        reward_days = int(row.reward_days or 0)
        for qualification_number in range(previous_qualified_count + 1, qualified_count_at_grant + 1):
            reward_days_by_qualification_number[qualification_number] = (
                reward_days_by_qualification_number.get(qualification_number, 0) + reward_days
            )
        previous_qualified_count = max(previous_qualified_count, qualified_count_at_grant)

    if not reward_days_by_qualification_number:
        return {}

    ranked_qualifications = (
        select(
            ReferralTrafficQualification.referral_id.label('referral_id'),
            func.row_number()
            .over(
                partition_by=ReferralTrafficQualification.referrer_id,
                order_by=(ReferralTrafficQualification.qualified_at.asc(), ReferralTrafficQualification.id.asc()),
            )
            .label('qualification_number'),
        )
        .where(ReferralTrafficQualification.referrer_id == referrer_id)
        .subquery()
    )

    query = select(ranked_qualifications.c.referral_id, ranked_qualifications.c.qualification_number).where(
        ranked_qualifications.c.qualification_number.in_(list(reward_days_by_qualification_number))
    )
    if referral_ids is not None:
        query = query.where(ranked_qualifications.c.referral_id.in_(referral_ids))

    result = await db.execute(query)
    reward_days_by_referral: dict[int, int] = {}
    for row in result.all():
        referral_id = int(row.referral_id)
        qualification_number = int(row.qualification_number)
        reward_days_by_referral[referral_id] = reward_days_by_referral.get(
            referral_id, 0
        ) + reward_days_by_qualification_number.get(qualification_number, 0)

    return reward_days_by_referral


async def get_traffic_qualifications_by_referral(
    db: AsyncSession,
    *,
    referral_ids: list[int],
) -> dict[int, ReferralTrafficQualification]:
    if not referral_ids:
        return {}

    result = await db.execute(
        select(ReferralTrafficQualification).where(ReferralTrafficQualification.referral_id.in_(referral_ids))
    )
    return {qualification.referral_id: qualification for qualification in result.scalars().all()}


async def get_attribution_modes_by_referral(
    db: AsyncSession,
    *,
    referral_ids: list[int],
) -> dict[int, str]:
    if not referral_ids:
        return {}

    result = await db.execute(select(ReferralAttribution).where(ReferralAttribution.referral_id.in_(referral_ids)))
    return {attribution.referral_id: attribution.mode for attribution in result.scalars().all()}


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
