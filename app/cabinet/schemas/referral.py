"""Referral program schemas for cabinet."""

from datetime import datetime

from pydantic import BaseModel, Field


class ReferralInfoResponse(BaseModel):
    """Referral program info for current user."""

    referral_code: str
    referral_link: str
    bot_referral_link: str = ''
    total_referrals: int
    active_referrals: int
    total_earnings_kopeks: int
    total_earnings_rubles: float
    commission_percent: int
    referral_reward_mode: str = 'balance_commission'
    available_reward_modes: list[str] = Field(default_factory=list)
    reward_mode_selectable: bool = False
    traffic_qualified_referrals: int = 0
    traffic_rewarded_referrals: int = 0
    traffic_unrewarded_referrals: int = 0
    traffic_reward_days_earned: int = 0
    traffic_reward_grants_count: int = 0
    available_balance_kopeks: int = 0
    available_balance_rubles: float = 0
    withdrawn_kopeks: int = 0


class ReferralItemResponse(BaseModel):
    """Single referral info."""

    id: int
    username: str | None = None
    first_name: str | None = None
    created_at: datetime
    has_subscription: bool
    has_paid: bool
    total_earned_kopeks: int = 0
    total_earned_rubles: float = 0
    reward_mode_at_registration: str | None = None
    traffic_qualified: bool = False
    traffic_qualified_at: datetime | None = None
    traffic_reward_days_earned: int = 0


class ReferralListResponse(BaseModel):
    """Paginated referral list."""

    items: list[ReferralItemResponse]
    total: int
    page: int
    per_page: int
    pages: int


class ReferralEarningResponse(BaseModel):
    """Referral earning history item."""

    id: int
    amount_kopeks: int
    amount_rubles: float
    reason: str
    referral_username: str | None = None
    referral_first_name: str | None = None
    campaign_name: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ReferralEarningsListResponse(BaseModel):
    """Paginated referral earnings list."""

    items: list[ReferralEarningResponse]
    total: int
    total_amount_kopeks: int
    total_amount_rubles: float
    page: int
    per_page: int
    pages: int


class ReferralTermsResponse(BaseModel):
    """Referral program terms."""

    is_enabled: bool
    available_reward_modes: list[str] = Field(default_factory=list)
    default_reward_mode: str = 'balance_commission'
    reward_mode_selectable: bool = False
    commission_percent: int
    first_payment_commission_percent: int | None = None
    recurring_commission_tiers: str = ''
    minimum_topup_kopeks: int
    minimum_topup_rubles: float
    first_topup_bonus_kopeks: int
    first_topup_bonus_rubles: float
    inviter_bonus_kopeks: int
    inviter_bonus_rubles: float
    max_commission_payments: int = 0
    partner_section_visible: bool = True
    traffic_rewards_enabled: bool = False
    traffic_reward_required_referrals: int = 1
    traffic_reward_days: int = 0
    traffic_reward_qualification_window_days: int = 0


class ReferralRewardModeUpdateRequest(BaseModel):
    """Request to update current user's referral reward mode."""

    mode: str
