from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.referral_traffic_reward import (
    count_qualifications,
    create_qualification,
    create_reward_grant,
    get_last_reward_grant,
    get_referral_attribution,
)
from app.database.crud.subscription import extend_subscription, get_subscription_by_user_id
from app.database.crud.user import get_user_by_id
from app.database.models import ReferralRewardMode, Subscription, User
from app.localization.texts import get_texts


logger = structlog.get_logger(__name__)


def _get_remnawave_uuid(user: User, subscription: Subscription | None) -> str | None:
    if subscription and subscription.remnawave_uuid:
        return subscription.remnawave_uuid
    return user.remnawave_uuid


def _is_outside_qualification_window(captured_at: datetime | None, window_days: int) -> bool:
    if not captured_at or window_days <= 0:
        return False
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=UTC)
    return datetime.now(UTC) > captured_at + timedelta(days=window_days)


async def _notify_referrer(bot: Bot | None, referrer: User, referral: User, reward_days: int) -> None:
    if not bot or not settings.REFERRAL_TRAFFIC_REWARD_NOTIFY:
        return

    try:
        from app.services.referral_service import send_referral_notification

        texts = get_texts(getattr(referrer, 'language', 'ru'))
        message = texts.t(
            'REFERRAL_TRAFFIC_REWARD_GRANTED',
            '🎁 <b>Реферальная награда начислена!</b>\n\n'
            'Ваши приглашённые пользователи начали пользоваться VPN.\n'
            'Подписка продлена на <b>{reward_days} дн.</b>\n\n'
            'Последний засчитанный реферал: <b>{referral_name}</b>',
        ).format(reward_days=reward_days, referral_name=referral.full_name)
        await send_referral_notification(
            bot,
            referrer.telegram_id,
            message,
            user=referrer,
            referral_name=referral.full_name,
        )
    except Exception as exc:
        logger.warning('Failed to notify referrer about traffic reward grant', referrer_id=referrer.id, error=exc)


async def process_first_connected(
    db: AsyncSession,
    *,
    user: User,
    subscription: Subscription | None,
    data: dict[str, Any] | None = None,
    bot: Bot | None = None,
) -> bool:
    """Process Remnawave `user.first_connected` as referral usage qualification.

    Returns True when the webhook was processed successfully or intentionally
    ignored. Returns False only for a real processing failure.
    """
    user_id = user.id
    referred_by_id = user.referred_by_id
    subscription_id = subscription.id if subscription else None

    if not settings.REFERRAL_TRAFFIC_REWARDS_ENABLED:
        logger.info('First-connected referral qualification skipped: traffic rewards disabled', user_id=user_id)
        return True

    if not referred_by_id:
        logger.info('First-connected referral qualification skipped: user has no referrer', user_id=user_id)
        return True

    if user_id == referred_by_id:
        logger.warning('Self-referral blocked in first-connected reward processing', user_id=user_id)
        return True

    remnawave_uuid = _get_remnawave_uuid(user, subscription)
    if not remnawave_uuid:
        logger.warning('First-connected referral qualification skipped: no Remnawave UUID', user_id=user_id)
        return True

    attribution = await get_referral_attribution(db, user_id)
    if not attribution:
        logger.info('First-connected referral qualification skipped: no captured attribution', user_id=user_id)
        return True

    attribution_mode = attribution.mode
    referrer_id = attribution.referrer_id
    mode_captured_at = attribution.mode_captured_at

    if attribution_mode != ReferralRewardMode.TRAFFIC_REWARD.value:
        logger.info(
            'First-connected referral qualification skipped: attribution mode is not traffic_reward',
            user_id=user_id,
            referrer_id=referrer_id,
            attribution_mode=attribution_mode,
        )
        return True

    qualification_window_days = settings.REFERRAL_TRAFFIC_REWARD_QUALIFICATION_WINDOW_DAYS
    if _is_outside_qualification_window(mode_captured_at, qualification_window_days):
        logger.info(
            'First-connected referral qualification skipped: outside qualification window',
            user_id=user_id,
            referrer_id=referrer_id,
            window_days=qualification_window_days,
        )
        return True

    qualification = await create_qualification(
        db,
        referrer_id=referrer_id,
        referral_id=user_id,
        subscription_id=subscription_id,
        remnawave_uuid=remnawave_uuid,
        source_event=str((data or {}).get('event') or 'user.first_connected'),
    )
    if not qualification:
        logger.info(
            'First-connected referral qualification already exists, continuing reward check',
            user_id=user_id,
            referrer_id=referrer_id,
        )

    required_referrals_count = max(1, settings.REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS)
    reward_days = settings.REFERRAL_TRAFFIC_REWARD_DAYS
    qualified_count = await count_qualifications(db, referrer_id=referrer_id)
    last_reward_grant = await get_last_reward_grant(db, referrer_id=referrer_id)
    last_reward_cycle = last_reward_grant.reward_cycle if last_reward_grant else 0
    rewarded_qualified_count = last_reward_grant.qualified_count_at_grant if last_reward_grant else 0
    unrewarded_qualified_count = max(0, qualified_count - rewarded_qualified_count)
    reward_cycles_to_grant = unrewarded_qualified_count // required_referrals_count
    if reward_cycles_to_grant < 1:
        logger.info(
            'First-connected referral qualified, reward threshold not reached yet',
            referrer_id=referrer_id,
            referral_id=user_id,
            qualified_count=qualified_count,
            rewarded_qualified_count=rewarded_qualified_count,
            unrewarded_qualified_count=unrewarded_qualified_count,
            required_referrals_count=required_referrals_count,
        )
        return True

    referrer = await get_user_by_id(db, referrer_id)
    if not referrer:
        logger.warning('First-connected reward skipped: referrer not found', referrer_id=referrer_id)
        return True

    referrer_subscription = await get_subscription_by_user_id(db, referrer.id)
    if not referrer_subscription:
        logger.warning('First-connected reward skipped: referrer has no subscription', referrer_id=referrer.id)
        return True

    granted_cycles = []
    updated_subscription = referrer_subscription
    for reward_offset in range(1, reward_cycles_to_grant + 1):
        reward_cycle = last_reward_cycle + reward_offset
        qualified_count_at_grant = rewarded_qualified_count + reward_offset * required_referrals_count
        grant = await create_reward_grant(
            db,
            referrer_id=referrer.id,
            reward_cycle=reward_cycle,
            qualified_count_at_grant=qualified_count_at_grant,
            reward_days=reward_days,
            subscription_id=updated_subscription.id,
        )
        if not grant:
            continue

        try:
            updated_subscription = await extend_subscription(db, updated_subscription, reward_days)
        except Exception as exc:
            logger.error(
                'Failed to extend referrer subscription after referral traffic reward grant',
                referrer_id=referrer.id,
                subscription_id=updated_subscription.id,
                grant_id=grant.id,
                reward_cycle=reward_cycle,
                error=exc,
            )
            return False

        granted_cycles.append((reward_cycle, grant))

    if not granted_cycles:
        return True

    try:
        from app.services.subscription_service import SubscriptionService

        await SubscriptionService().update_remnawave_user(db, updated_subscription)
    except Exception as exc:
        logger.warning(
            'Failed to sync Remnawave after referral traffic reward grant',
            referrer_id=referrer.id,
            subscription_id=updated_subscription.id,
            reward_cycles=[reward_cycle for reward_cycle, _grant in granted_cycles],
            error=exc,
        )

    total_reward_days = reward_days * len(granted_cycles)
    await _notify_referrer(bot, referrer, user, total_reward_days)
    logger.info(
        'Referral first-connected reward granted',
        referrer_id=referrer.id,
        referral_id=user.id,
        reward_days=total_reward_days,
        qualified_count=qualified_count,
        reward_cycles=[reward_cycle for reward_cycle, _grant in granted_cycles],
        grant_ids=[grant.id for _reward_cycle, grant in granted_cycles],
    )
    return True
