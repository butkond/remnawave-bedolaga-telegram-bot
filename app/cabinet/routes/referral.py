"""Referral program routes for cabinet."""

import math

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.crud.referral_traffic_reward import (
    get_attribution_modes_by_referral,
    get_traffic_qualifications_by_referral,
    get_traffic_reward_days_by_referral,
    get_traffic_reward_summary,
)
from app.database.models import (
    AdvertisingCampaign,
    ReferralEarning,
    Subscription,
    SubscriptionStatus,
    User,
    WithdrawalRequest,
    WithdrawalRequestStatus,
)

from ..dependencies import get_cabinet_db, get_current_cabinet_user
from ..schemas.referral import (
    ReferralEarningResponse,
    ReferralEarningsListResponse,
    ReferralInfoResponse,
    ReferralItemResponse,
    ReferralListResponse,
    ReferralRewardModeUpdateRequest,
    ReferralTermsResponse,
)


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/referral', tags=['Cabinet Referral'])


@router.get('', response_model=ReferralInfoResponse)
async def get_referral_info(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get referral program info for current user."""
    # Get total referrals count
    total_query = select(func.count()).select_from(User).where(User.referred_by_id == user.id)
    total_result = await db.execute(total_query)
    total_referrals = total_result.scalar() or 0

    # Get active referrals (with active subscription right now)
    active_query = (
        select(func.count(func.distinct(User.id)))
        .join(Subscription, User.id == Subscription.user_id)
        .where(
            User.referred_by_id == user.id,
            Subscription.status == SubscriptionStatus.ACTIVE.value,
            Subscription.end_date > func.now(),
        )
    )
    active_result = await db.execute(active_query)
    active_referrals = active_result.scalar() or 0

    # Get total earnings
    earnings_query = select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0)).where(
        ReferralEarning.user_id == user.id
    )
    earnings_result = await db.execute(earnings_query)
    total_earnings = earnings_result.scalar() or 0

    # Get user's commission percent
    commission_percent = user.referral_commission_percent
    if commission_percent is None:
        commission_percent = settings.REFERRAL_COMMISSION_PERCENT

    # Get withdrawn amount (approved + completed withdrawal requests)
    withdrawn_query = select(func.coalesce(func.sum(WithdrawalRequest.amount_kopeks), 0)).where(
        WithdrawalRequest.user_id == user.id,
        WithdrawalRequest.status.in_([WithdrawalRequestStatus.APPROVED.value, WithdrawalRequestStatus.COMPLETED.value]),
    )
    withdrawn_result = await db.execute(withdrawn_query)
    withdrawn = withdrawn_result.scalar() or 0

    # Get pending withdrawal amount
    pending_query = select(func.coalesce(func.sum(WithdrawalRequest.amount_kopeks), 0)).where(
        WithdrawalRequest.user_id == user.id,
        WithdrawalRequest.status == WithdrawalRequestStatus.PENDING.value,
    )
    pending_result = await db.execute(pending_query)
    pending = pending_result.scalar() or 0

    # Доступный баланс: мин(кошелёк, заработано - выведено - в ожидании)
    referral_entitlement = max(0, total_earnings - withdrawn - pending)
    available_balance = min(user.balance_kopeks, referral_entitlement)

    # Build referral links
    referral_link = (settings.get_cabinet_referral_link(user.referral_code) or '') if user.referral_code else ''
    bot_referral_link = settings.get_bot_referral_link(user.referral_code) if user.referral_code else ''
    available_reward_modes = settings.get_available_referral_reward_modes()
    traffic_summary = await get_traffic_reward_summary(db, referrer_id=user.id)

    return ReferralInfoResponse(
        referral_code=user.referral_code or '',
        referral_link=referral_link,
        bot_referral_link=bot_referral_link,
        total_referrals=total_referrals,
        active_referrals=active_referrals,
        total_earnings_kopeks=total_earnings,
        total_earnings_rubles=total_earnings / 100,
        commission_percent=commission_percent,
        referral_reward_mode=settings.get_user_referral_reward_mode(user),
        available_reward_modes=available_reward_modes,
        reward_mode_selectable=settings.is_referral_reward_mode_selectable(),
        traffic_qualified_referrals=traffic_summary['qualified_referrals_count'],
        traffic_rewarded_referrals=traffic_summary['rewarded_qualified_count'],
        traffic_unrewarded_referrals=traffic_summary['unrewarded_qualified_count'],
        traffic_reward_days_earned=traffic_summary['traffic_reward_days_earned'],
        traffic_reward_grants_count=traffic_summary['traffic_reward_grants_count'],
        available_balance_kopeks=available_balance,
        available_balance_rubles=available_balance / 100,
        withdrawn_kopeks=withdrawn,
    )


@router.get('/list', response_model=ReferralListResponse)
async def get_referral_list(
    page: int = Query(1, ge=1, description='Page number'),
    per_page: int = Query(20, ge=1, le=100, description='Items per page'),
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get list of invited users."""
    # Base query with eager loading of subscription relationship
    query = (
        select(User)
        .options(selectinload(User.subscriptions).selectinload(Subscription.tariff))
        .where(User.referred_by_id == user.id)
    )

    # Get total count
    count_query = select(func.count()).select_from(User).where(User.referred_by_id == user.id)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    offset = (page - 1) * per_page
    query = query.order_by(desc(User.created_at)).offset(offset).limit(per_page)

    result = await db.execute(query)
    referrals = result.scalars().all()
    referral_ids = [referral.id for referral in referrals]

    earnings_map: dict[int, int] = {}
    if referral_ids:
        earnings_result = await db.execute(
            select(
                ReferralEarning.referral_id,
                func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0).label('earned'),
            )
            .where(
                ReferralEarning.user_id == user.id,
                ReferralEarning.referral_id.in_(referral_ids),
            )
            .group_by(ReferralEarning.referral_id)
        )
        earnings_map = {int(row.referral_id): int(row.earned or 0) for row in earnings_result.all()}

    attribution_modes = await get_attribution_modes_by_referral(db, referral_ids=referral_ids)
    traffic_qualifications = await get_traffic_qualifications_by_referral(db, referral_ids=referral_ids)
    traffic_reward_days = await get_traffic_reward_days_by_referral(db, referrer_id=user.id, referral_ids=referral_ids)

    items = [
        ReferralItemResponse(
            id=r.id,
            username=r.username,
            first_name=r.first_name,
            created_at=r.created_at,
            has_subscription=bool(getattr(r, 'subscriptions', None)),
            has_paid=r.has_had_paid_subscription,
            total_earned_kopeks=earnings_map.get(r.id, 0),
            total_earned_rubles=earnings_map.get(r.id, 0) / 100,
            reward_mode_at_registration=attribution_modes.get(r.id),
            traffic_qualified=r.id in traffic_qualifications,
            traffic_qualified_at=traffic_qualifications[r.id].qualified_at if r.id in traffic_qualifications else None,
            traffic_reward_days_earned=traffic_reward_days.get(r.id, 0),
        )
        for r in referrals
    ]

    pages = math.ceil(total / per_page) if total > 0 else 1

    return ReferralListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get('/earnings', response_model=ReferralEarningsListResponse)
async def get_referral_earnings(
    page: int = Query(1, ge=1, description='Page number'),
    per_page: int = Query(20, ge=1, le=100, description='Items per page'),
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get referral earnings history."""
    # Base query
    query = select(ReferralEarning).where(ReferralEarning.user_id == user.id)

    # Get total count and sum
    count_query = select(func.count()).select_from(ReferralEarning).where(ReferralEarning.user_id == user.id)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    sum_query = select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0)).where(
        ReferralEarning.user_id == user.id
    )
    sum_result = await db.execute(sum_query)
    total_amount = sum_result.scalar() or 0

    # Paginate
    offset = (page - 1) * per_page
    query = query.order_by(desc(ReferralEarning.created_at)).offset(offset).limit(per_page)

    result = await db.execute(query)
    earnings = result.scalars().all()

    # Batch-fetch referral users to avoid N+1
    referral_ids = list({e.referral_id for e in earnings if e.referral_id})
    if referral_ids:
        referral_users_result = await db.execute(select(User).where(User.id.in_(referral_ids)))
        referral_users_map = {u.id: u for u in referral_users_result.scalars().all()}
    else:
        referral_users_map = {}

    # Batch-fetch campaigns to avoid N+1
    campaign_ids = list({e.campaign_id for e in earnings if e.campaign_id})
    if campaign_ids:
        campaigns_result = await db.execute(select(AdvertisingCampaign).where(AdvertisingCampaign.id.in_(campaign_ids)))
        campaigns_map = {c.id: c for c in campaigns_result.scalars().all()}
    else:
        campaigns_map = {}

    items = []
    for e in earnings:
        referral_user = referral_users_map.get(e.referral_id) if e.referral_id else None
        campaign = campaigns_map.get(e.campaign_id) if e.campaign_id else None

        items.append(
            ReferralEarningResponse(
                id=e.id,
                amount_kopeks=e.amount_kopeks,
                amount_rubles=e.amount_kopeks / 100,
                reason=e.reason or 'Referral commission',
                referral_username=referral_user.username if referral_user else None,
                referral_first_name=referral_user.first_name if referral_user else None,
                campaign_name=campaign.name if campaign else None,
                created_at=e.created_at,
            )
        )

    pages = math.ceil(total / per_page) if total > 0 else 1

    return ReferralEarningsListResponse(
        items=items,
        total=total,
        total_amount_kopeks=total_amount,
        total_amount_rubles=total_amount / 100,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get('/terms', response_model=ReferralTermsResponse)
async def get_referral_terms():
    """Get referral program terms."""
    return ReferralTermsResponse(
        is_enabled=settings.is_referral_program_enabled(),
        available_reward_modes=settings.get_available_referral_reward_modes(),
        default_reward_mode=settings.get_default_referral_reward_mode(),
        reward_mode_selectable=settings.is_referral_reward_mode_selectable(),
        commission_percent=settings.REFERRAL_COMMISSION_PERCENT,
        first_payment_commission_percent=settings.REFERRAL_FIRST_PAYMENT_COMMISSION_PERCENT,
        recurring_commission_tiers=settings.REFERRAL_RECURRING_COMMISSION_TIERS,
        minimum_topup_kopeks=settings.REFERRAL_MINIMUM_TOPUP_KOPEKS,
        minimum_topup_rubles=settings.REFERRAL_MINIMUM_TOPUP_KOPEKS / 100,
        first_topup_bonus_kopeks=settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS,
        first_topup_bonus_rubles=settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS / 100,
        inviter_bonus_kopeks=settings.REFERRAL_INVITER_BONUS_KOPEKS,
        inviter_bonus_rubles=settings.REFERRAL_INVITER_BONUS_KOPEKS / 100,
        max_commission_payments=settings.REFERRAL_MAX_COMMISSION_PAYMENTS,
        partner_section_visible=settings.REFERRAL_PARTNER_SECTION_VISIBLE,
        traffic_rewards_enabled=settings.REFERRAL_TRAFFIC_REWARDS_ENABLED,
        traffic_reward_required_referrals=settings.REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS,
        traffic_reward_days=settings.REFERRAL_TRAFFIC_REWARD_DAYS,
        traffic_reward_qualification_window_days=settings.REFERRAL_TRAFFIC_REWARD_QUALIFICATION_WINDOW_DAYS,
    )


@router.patch('/reward-mode', response_model=ReferralInfoResponse)
async def update_referral_reward_mode(
    request: ReferralRewardModeUpdateRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update current user's active referral reward attribution mode."""
    mode = (request.mode or '').strip().lower()
    if not settings.is_referral_reward_mode_selectable():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Referral reward mode selection is not available',
        )
    if not settings.is_referral_reward_mode_available(mode):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Referral reward mode is not available',
        )

    user.referral_reward_mode = mode
    await db.commit()
    await db.refresh(user)

    return await get_referral_info(user=user, db=db)
