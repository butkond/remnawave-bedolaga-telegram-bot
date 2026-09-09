import structlog

from app.utils.cache import UserCache


logger = structlog.get_logger(__name__)


_CHECKOUT_SESSION_KEY = 'subscription_checkout'
_CHECKOUT_TTL_SECONDS = 3600


async def save_subscription_checkout_draft(user_id: int, data: dict, ttl: int = _CHECKOUT_TTL_SECONDS) -> bool:
    """Persist subscription checkout draft data in cache."""

    return await UserCache.set_user_session(user_id, _CHECKOUT_SESSION_KEY, data, ttl)


async def get_subscription_checkout_draft(user_id: int) -> dict | None:
    """Retrieve subscription checkout draft from cache."""

    return await UserCache.get_user_session(user_id, _CHECKOUT_SESSION_KEY)


async def clear_subscription_checkout_draft(user_id: int) -> bool:
    """Remove stored subscription checkout draft for the user."""

    return await UserCache.delete_user_session(user_id, _CHECKOUT_SESSION_KEY)


async def has_subscription_checkout_draft(user_id: int) -> bool:
    draft = await get_subscription_checkout_draft(user_id)
    return draft is not None


def should_offer_checkout_resume(
    user,
    has_draft: bool,
    *,
    subscription=None,
) -> bool:
    """Checkout resume button is disabled for this customization."""

    return False
