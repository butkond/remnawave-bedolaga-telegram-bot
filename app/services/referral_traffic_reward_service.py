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
    get_referral_attribution,
    get_reward_grant,
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
    if not settings.REFERRAL_TRAFFIC_REWARDS_ENABLED:
        return True

    if not user.referred_by_id:
        return True

    if user.id == user.referred_by_id:
        logger.warning('Self-referral blocked in first-connected reward processing', user_id=user.id)
        return True

    remnawave_uuid = _get_remnawave_uuid(user, subscription)
    if not remnawave_uuid:
        logger.warning('First-connected referral qualification skipped: no Remnawave UUID', user_id=user.id)
        return True

    attribution = await get_referral_attribution(db, user.id)
    if not attribution:
        logger.info('First-connected referral qualification skipped: no captured attribution', user_id=user.id)
        return True

    if attribution.mode != ReferralRewardMode.TRAFFIC_REWARD.value:
        return True

    qualification_window_days = settings.REFERRAL_TRAFFIC_REWARD_QUALIFICATION_WINDOW_DAYS
    if _is_outside_qualification_window(attribution.mode_captured_at, qualification_window_days):
        logger.info(
            'First-connected referral qualification skipped: outside qualification window',
            user_id=user.id,
            referrer_id=attribution.referrer_id,
            window_days=qualification_window_days,
        )
        return True

    qualification = await create_qualification(
        db,
        referrer_id=attribution.referrer_id,
        referral_id=user.id,
        subscription_id=subscription.id if subscription else None,
        remnawave_uuid=remnawave_uuid,
        source_event=str((data or {}).get('event') or 'user.first_connected'),
    )
    if not qualification:
        return True

    required_referrals_count = settings.REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS
    reward_days = settings.REFERRAL_TRAFFIC_REWARD_DAYS
    qualified_count = await count_qualifications(db, referrer_id=attribution.referrer_id)
    if qualified_count < required_referrals_count:
        logger.info(
            'First-connected referral qualified, reward threshold not reached yet',
            referrer_id=attribution.referrer_id,
            referral_id=user.id,
            qualified_count=qualified_count,
            required_referrals_count=required_referrals_count,
        )
        return True

    if await get_reward_grant(db, referrer_id=attribution.referrer_id):
        return True

    referrer = await get_user_by_id(db, attribution.referrer_id)
    if not referrer:
        logger.warning('First-connected reward skipped: referrer not found', referrer_id=attribution.referrer_id)
        return True

    referrer_subscription = await get_subscription_by_user_id(db, referrer.id)
    if not referrer_subscription:
        logger.warning('First-connected reward skipped: referrer has no subscription', referrer_id=referrer.id)
        return True

    grant = await create_reward_grant(
        db,
        referrer_id=referrer.id,
        qualified_count_at_grant=qualified_count,
        reward_days=reward_days,
        subscription_id=referrer_subscription.id,
    )
    if not grant:
        return True

    try:
        updated_subscription = await extend_subscription(db, referrer_subscription, reward_days)
    except Exception as exc:
        logger.error(
            'Failed to extend referrer subscription after referral traffic reward grant',
            referrer_id=referrer.id,
            subscription_id=referrer_subscription.id,
            grant_id=grant.id,
            error=exc,
        )
        return False

    try:
        from app.services.subscription_service import SubscriptionService

        await SubscriptionService().update_remnawave_user(db, updated_subscription)
    except Exception as exc:
        logger.warning(
            'Failed to sync Remnawave after referral traffic reward grant',
            referrer_id=referrer.id,
            subscription_id=updated_subscription.id,
            grant_id=grant.id,
            error=exc,
        )

    await _notify_referrer(bot, referrer, user, reward_days)
    logger.info(
        'Referral first-connected reward granted',
        referrer_id=referrer.id,
        referral_id=user.id,
        reward_days=reward_days,
        qualified_count=qualified_count,
        grant_id=grant.id,
    )
    return True
