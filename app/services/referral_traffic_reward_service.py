from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.referral_traffic_reward import (
    count_referrer_traffic_qualifications,
    create_referral_attribution,
    create_referral_traffic_qualification,
    create_referral_traffic_reward_grant,
    get_referral_attribution_by_referral_id,
    get_referral_traffic_qualification_by_referral_id,
    get_referral_traffic_reward_grant_by_referrer_id,
)
from app.database.crud.subscription import extend_subscription, get_subscription_by_user_id
from app.database.crud.user import get_user_by_id
from app.database.models import ReferralRewardMode, Subscription, User
from app.localization.texts import get_texts


logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class ReferralTrafficRewardResult:
    qualified: bool = False
    granted: bool = False


class ReferralTrafficRewardService:
    def get_available_modes(self) -> list[str]:
        if not settings.is_referral_program_enabled():
            return []

        modes: list[str] = []
        if settings.is_referral_balance_commission_enabled():
            modes.append(ReferralRewardMode.BALANCE_COMMISSION.value)
        if settings.is_referral_traffic_rewards_enabled():
            modes.append(ReferralRewardMode.TRAFFIC_REWARD.value)
        return modes

    def get_default_mode(self) -> str:
        if not settings.is_referral_program_enabled():
            return ReferralRewardMode.BALANCE_COMMISSION.value

        if settings.is_referral_balance_commission_enabled():
            return ReferralRewardMode.BALANCE_COMMISSION.value
        if settings.is_referral_traffic_rewards_enabled():
            return ReferralRewardMode.TRAFFIC_REWARD.value
        return ReferralRewardMode.BALANCE_COMMISSION.value

    def normalize_mode(self, mode: str | None) -> str:
        if mode in self.get_available_modes():
            return mode or self.get_default_mode()
        return self.get_default_mode()

    def get_mode_title(self, language: str, mode: str | None) -> str:
        texts = get_texts(language)
        normalized = self.normalize_mode(mode)
        if normalized == ReferralRewardMode.TRAFFIC_REWARD.value:
            return texts.t(
                'REFERRAL_MODE_TRAFFIC_REWARD',
                '📊 Traffic rewards',
            )
        return texts.t(
            'REFERRAL_MODE_BALANCE_COMMISSION',
            '💰 Balance commission',
        )

    def get_other_mode(self, mode: str | None) -> str:
        normalized = self.normalize_mode(mode)
        if normalized == ReferralRewardMode.TRAFFIC_REWARD.value:
            return ReferralRewardMode.BALANCE_COMMISSION.value
        return ReferralRewardMode.TRAFFIC_REWARD.value

    def has_mode_selection(self) -> bool:
        return settings.is_referral_program_enabled() and settings.has_referral_mode_selection()

    async def get_referrer_mode(self, referrer: User) -> str:
        return self.normalize_mode(referrer.referral_reward_mode)

    async def set_referrer_mode(self, db: AsyncSession, referrer: User, mode: str) -> str:
        normalized = self.normalize_mode(mode)
        referrer.referral_reward_mode = normalized
        referrer.referral_reward_mode_updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(referrer)
        return normalized

    def can_switch_referrer_mode(self, referrer: User) -> tuple[bool, int]:
        cooldown_hours = int(settings.REFERRAL_MODE_SWITCH_COOLDOWN_HOURS or 0)
        if cooldown_hours <= 0 or not referrer.referral_reward_mode_updated_at:
            return True, 0

        elapsed = datetime.now(UTC) - referrer.referral_reward_mode_updated_at
        cooldown = timedelta(hours=cooldown_hours)
        if elapsed >= cooldown:
            return True, 0

        remaining = cooldown - elapsed
        remaining_hours = int(remaining.total_seconds() // 3600)
        if remaining.total_seconds() % 3600:
            remaining_hours += 1
        return False, max(1, remaining_hours)

    async def capture_referral_attribution(
        self,
        db: AsyncSession,
        *,
        referral: User,
        referrer: User,
    ):
        existing = await get_referral_attribution_by_referral_id(db, referral.id)
        if existing:
            return existing

        mode = self.normalize_mode(referrer.referral_reward_mode)
        if not referrer.referral_reward_mode:
            referrer.referral_reward_mode = mode

        return await create_referral_attribution(
            db,
            referral_id=referral.id,
            referrer_id=referrer.id,
            referral_code=referrer.referral_code,
            mode=mode,
            commit=False,
        )

    async def process_subscription_traffic_update(
        self,
        db: AsyncSession,
        subscription: Subscription,
        old_used_gb: float,
        new_used_gb: float,
        bot=None,
    ) -> ReferralTrafficRewardResult:
        if not settings.is_referral_traffic_rewards_enabled():
            return ReferralTrafficRewardResult()

        threshold = float(settings.REFERRAL_TRAFFIC_REWARD_THRESHOLD_GB or 0)
        if threshold <= 0:
            return ReferralTrafficRewardResult()

        if new_used_gb < threshold or new_used_gb <= old_used_gb:
            return ReferralTrafficRewardResult()

        user = await get_user_by_id(db, subscription.user_id)
        if not user or not user.referred_by_id or user.referred_by_id == user.id:
            return ReferralTrafficRewardResult()

        attribution = await get_referral_attribution_by_referral_id(db, user.id)
        if not attribution:
            return ReferralTrafficRewardResult()

        if attribution.mode != ReferralRewardMode.TRAFFIC_REWARD.value:
            return ReferralTrafficRewardResult()

        window_days = int(settings.REFERRAL_TRAFFIC_REWARD_QUALIFICATION_WINDOW_DAYS or 0)
        if window_days > 0 and user.created_at:
            if datetime.now(UTC) - user.created_at > timedelta(days=window_days):
                return ReferralTrafficRewardResult()

        remnawave_uuid = subscription.remnawave_uuid or user.remnawave_uuid
        if not remnawave_uuid:
            logger.debug(
                'Referral traffic reward skipped: missing remnawave uuid',
                subscription_id=subscription.id,
                user_id=user.id,
            )
            return ReferralTrafficRewardResult()

        existing_qualification = await get_referral_traffic_qualification_by_referral_id(db, user.id)
        if existing_qualification:
            return ReferralTrafficRewardResult(qualified=True)

        qualification = await create_referral_traffic_qualification(
            db,
            referrer_id=user.referred_by_id,
            referral_id=user.id,
            subscription_id=subscription.id,
            remnawave_uuid=remnawave_uuid,
            traffic_used_gb=new_used_gb,
            commit=False,
        )
        if not qualification:
            return ReferralTrafficRewardResult()

        result = ReferralTrafficRewardResult(qualified=True)

        qualified_count = await count_referrer_traffic_qualifications(db, user.referred_by_id)
        required_count = int(settings.REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS or 0)
        if qualified_count < required_count:
            return result

        existing_grant = await get_referral_traffic_reward_grant_by_referrer_id(db, user.referred_by_id)
        if existing_grant:
            return result

        referrer = await get_user_by_id(db, user.referred_by_id)
        if not referrer:
            return result

        reward_subscription = await get_subscription_by_user_id(db, referrer.id)
        if not reward_subscription:
            return result

        reward_days = int(settings.REFERRAL_TRAFFIC_REWARD_DAYS or 0)
        if reward_days <= 0:
            return result

        await extend_subscription(db, reward_subscription, reward_days, commit=False)
        grant = await create_referral_traffic_reward_grant(
            db,
            referrer_id=referrer.id,
            qualified_count_at_grant=qualified_count,
            reward_days=reward_days,
            subscription_id=reward_subscription.id,
            commit=False,
        )
        if not grant:
            return result

        result.granted = True

        try:
            from app.services.subscription_service import SubscriptionService

            subscription_service = SubscriptionService()
            await subscription_service.update_remnawave_user(db, reward_subscription, reset_traffic=False)
        except Exception as error:  # pragma: no cover - defensive logging
            logger.warning('Failed to sync rewarded subscription with RemnaWave', error=error)

        if bot and settings.REFERRAL_TRAFFIC_REWARD_NOTIFY and referrer.telegram_id:
            texts = get_texts(referrer.language)
            message = texts.t(
                'REFERRAL_TRAFFIC_REWARD_GRANTED',
                '🎉 <b>Награда за трафик</b>\n\n'
                'У вас {count} квалифицированных приглашённых.\n'
                'Подписка продлена на {days} дней.',
            ).format(count=qualified_count, days=reward_days)
            try:
                await bot.send_message(referrer.telegram_id, message, parse_mode='HTML')
            except Exception as error:  # pragma: no cover - notification best effort
                logger.warning('Failed to notify referrer about traffic reward', error=error, referrer_id=referrer.id)

        return result


referral_traffic_reward_service = ReferralTrafficRewardService()
