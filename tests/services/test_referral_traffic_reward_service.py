from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_referral_reward_modes_from_settings(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, 'REFERRAL_PROGRAM_ENABLED', True)
    monkeypatch.setattr(settings, 'REFERRAL_REWARD_MODES', 'balance_commission,traffic_reward')
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True)
    monkeypatch.setattr(settings, 'REFERRAL_DEFAULT_REWARD_MODE', 'traffic_reward')

    user = SimpleNamespace(referral_reward_mode='balance_commission')

    assert settings.get_available_referral_reward_modes() == ['balance_commission', 'traffic_reward']
    assert settings.is_referral_reward_mode_selectable() is True
    assert settings.get_default_referral_reward_mode() == 'traffic_reward'
    assert settings.get_user_referral_reward_mode(user) == 'balance_commission'


def test_referral_reward_modes_hide_unavailable_traffic(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, 'REFERRAL_PROGRAM_ENABLED', True)
    monkeypatch.setattr(settings, 'REFERRAL_REWARD_MODES', 'traffic_reward')
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', False)

    assert settings.get_available_referral_reward_modes() == []
    assert settings.is_referral_program_enabled() is False


def test_referral_reward_modes_allow_traffic_only_without_choice(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, 'REFERRAL_PROGRAM_ENABLED', True)
    monkeypatch.setattr(settings, 'REFERRAL_REWARD_MODES', 'traffic_reward')
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True)
    monkeypatch.setattr(settings, 'REFERRAL_DEFAULT_REWARD_MODE', 'balance_commission')
    monkeypatch.setattr(settings, 'REFERRAL_WITHDRAWAL_ENABLED', True)

    assert settings.get_available_referral_reward_modes() == ['traffic_reward']
    assert settings.is_referral_program_enabled() is True
    assert settings.is_referral_reward_mode_selectable() is False
    assert settings.get_default_referral_reward_mode() == 'traffic_reward'
    assert settings.is_referral_withdrawal_enabled() is False


@pytest.mark.asyncio
async def test_capture_referral_attribution_uses_referrer_selected_mode(monkeypatch):
    from app.services import referral_service

    monkeypatch.setattr(referral_service.settings, 'REFERRAL_REWARD_MODES', 'balance_commission,traffic_reward')
    monkeypatch.setattr(referral_service.settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True)
    monkeypatch.setattr(referral_service.settings, 'REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS', 3)
    monkeypatch.setattr(referral_service.settings, 'REFERRAL_TRAFFIC_REWARD_DAYS', 7)
    monkeypatch.setattr(referral_service.settings, 'REFERRAL_TRAFFIC_REWARD_QUALIFICATION_WINDOW_DAYS', 0)

    db = AsyncMock()
    new_user = SimpleNamespace(id=10)
    referrer = SimpleNamespace(id=20, referral_code='REF20', referral_reward_mode='traffic_reward')
    ensure_mock = AsyncMock()
    monkeypatch.setattr(referral_service, 'ensure_referral_attribution', ensure_mock)

    await referral_service._capture_referral_attribution(db, new_user=new_user, referrer=referrer)

    ensure_mock.assert_awaited_once_with(
        db,
        referral_id=10,
        referrer_id=20,
        referral_code='REF20',
        mode=referral_service.ReferralRewardMode.TRAFFIC_REWARD,
    )


@pytest.mark.asyncio
async def test_first_connected_duplicate_qualification_does_not_grant(monkeypatch):
    from app.services import referral_traffic_reward_service as svc

    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS', 2)

    db = AsyncMock()
    user = SimpleNamespace(id=10, referred_by_id=20, remnawave_uuid='user-uuid')
    subscription = SimpleNamespace(id=30, remnawave_uuid='sub-uuid')
    attribution = SimpleNamespace(mode='traffic_reward', referrer_id=20, mode_captured_at=None)

    monkeypatch.setattr(svc, 'get_referral_attribution', AsyncMock(return_value=attribution))
    monkeypatch.setattr(svc, 'create_qualification', AsyncMock(return_value=None))
    count_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(svc, 'count_qualifications', count_mock)
    grant_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(svc, 'get_last_reward_grant', grant_mock)

    result = await svc.process_first_connected(db, user=user, subscription=subscription)

    assert result is True
    count_mock.assert_awaited_once_with(db, referrer_id=20)
    grant_mock.assert_awaited_once_with(db, referrer_id=20)


@pytest.mark.asyncio
async def test_first_connected_duplicate_qualification_grants_when_threshold_reached(monkeypatch):
    from app.services import referral_traffic_reward_service as svc

    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS', 1)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_DAYS', 7)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_QUALIFICATION_WINDOW_DAYS', 0)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_NOTIFY', False)

    db = AsyncMock()
    user = SimpleNamespace(id=10, referred_by_id=20, remnawave_uuid='user-uuid', full_name='Referral')
    subscription = SimpleNamespace(id=30, remnawave_uuid='sub-uuid')
    referrer = SimpleNamespace(id=20, telegram_id=200, full_name='Referrer')
    referrer_subscription = SimpleNamespace(id=40)
    updated_subscription = SimpleNamespace(id=40)
    attribution = SimpleNamespace(mode='traffic_reward', referrer_id=20, mode_captured_at=None)
    grant = SimpleNamespace(id=50)

    monkeypatch.setattr(svc, 'get_referral_attribution', AsyncMock(return_value=attribution))
    monkeypatch.setattr(svc, 'create_qualification', AsyncMock(return_value=None))
    monkeypatch.setattr(svc, 'count_qualifications', AsyncMock(return_value=1))
    monkeypatch.setattr(svc, 'get_last_reward_grant', AsyncMock(return_value=None))
    monkeypatch.setattr(svc, 'get_user_by_id', AsyncMock(return_value=referrer))
    monkeypatch.setattr(svc, 'get_subscription_by_user_id', AsyncMock(return_value=referrer_subscription))
    monkeypatch.setattr(svc, 'create_reward_grant', AsyncMock(return_value=grant))
    extend_mock = AsyncMock(return_value=updated_subscription)
    monkeypatch.setattr(svc, 'extend_subscription', extend_mock)

    update_remnawave_user = AsyncMock()
    monkeypatch.setattr(
        'app.services.subscription_service.SubscriptionService',
        lambda: SimpleNamespace(update_remnawave_user=update_remnawave_user),
    )

    result = await svc.process_first_connected(db, user=user, subscription=subscription)

    assert result is True
    svc.create_reward_grant.assert_awaited_once_with(
        db,
        referrer_id=20,
        reward_cycle=1,
        qualified_count_at_grant=1,
        reward_days=7,
        subscription_id=40,
    )
    extend_mock.assert_awaited_once_with(db, referrer_subscription, 7)
    update_remnawave_user.assert_awaited_once_with(db, updated_subscription)


@pytest.mark.asyncio
async def test_first_connected_grants_reward_when_threshold_reached(monkeypatch):
    from app.services import referral_traffic_reward_service as svc

    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS', 2)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_DAYS', 7)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_QUALIFICATION_WINDOW_DAYS', 0)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_NOTIFY', False)

    db = AsyncMock()
    user = SimpleNamespace(id=10, referred_by_id=20, remnawave_uuid='user-uuid', full_name='Referral')
    subscription = SimpleNamespace(id=30, remnawave_uuid='sub-uuid')
    referrer = SimpleNamespace(id=20, telegram_id=200, full_name='Referrer')
    referrer_subscription = SimpleNamespace(id=40)
    updated_subscription = SimpleNamespace(id=40)
    attribution = SimpleNamespace(mode='traffic_reward', referrer_id=20, mode_captured_at=None)
    grant = SimpleNamespace(id=50)

    monkeypatch.setattr(svc, 'get_referral_attribution', AsyncMock(return_value=attribution))
    monkeypatch.setattr(svc, 'create_qualification', AsyncMock(return_value=SimpleNamespace(id=60)))
    monkeypatch.setattr(svc, 'count_qualifications', AsyncMock(return_value=2))
    monkeypatch.setattr(svc, 'get_last_reward_grant', AsyncMock(return_value=None))
    monkeypatch.setattr(svc, 'get_user_by_id', AsyncMock(return_value=referrer))
    monkeypatch.setattr(svc, 'get_subscription_by_user_id', AsyncMock(return_value=referrer_subscription))
    monkeypatch.setattr(svc, 'create_reward_grant', AsyncMock(return_value=grant))
    extend_mock = AsyncMock(return_value=updated_subscription)
    monkeypatch.setattr(svc, 'extend_subscription', extend_mock)

    update_remnawave_user = AsyncMock()
    monkeypatch.setattr(
        'app.services.subscription_service.SubscriptionService',
        lambda: SimpleNamespace(update_remnawave_user=update_remnawave_user),
    )

    result = await svc.process_first_connected(db, user=user, subscription=subscription)

    assert result is True
    svc.create_reward_grant.assert_awaited_once_with(
        db,
        referrer_id=20,
        reward_cycle=1,
        qualified_count_at_grant=2,
        reward_days=7,
        subscription_id=40,
    )
    extend_mock.assert_awaited_once_with(db, referrer_subscription, 7)
    update_remnawave_user.assert_awaited_once_with(db, updated_subscription)


@pytest.mark.asyncio
async def test_first_connected_grants_next_reward_cycle_when_threshold_reached_again(monkeypatch):
    from app.services import referral_traffic_reward_service as svc

    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS', 2)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_DAYS', 7)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_QUALIFICATION_WINDOW_DAYS', 0)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_NOTIFY', False)

    db = AsyncMock()
    user = SimpleNamespace(id=12, referred_by_id=20, remnawave_uuid='user-uuid-2', full_name='Referral 2')
    subscription = SimpleNamespace(id=32, remnawave_uuid='sub-uuid-2')
    referrer = SimpleNamespace(id=20, telegram_id=200, full_name='Referrer')
    referrer_subscription = SimpleNamespace(id=40)
    updated_subscription = SimpleNamespace(id=40)
    attribution = SimpleNamespace(mode='traffic_reward', referrer_id=20, mode_captured_at=None)
    first_cycle_grant = SimpleNamespace(id=50, reward_cycle=1, qualified_count_at_grant=2)
    second_cycle_grant = SimpleNamespace(id=51, reward_cycle=2)

    monkeypatch.setattr(svc, 'get_referral_attribution', AsyncMock(return_value=attribution))
    monkeypatch.setattr(svc, 'create_qualification', AsyncMock(return_value=SimpleNamespace(id=61)))
    monkeypatch.setattr(svc, 'count_qualifications', AsyncMock(return_value=4))
    monkeypatch.setattr(svc, 'get_last_reward_grant', AsyncMock(return_value=first_cycle_grant))
    monkeypatch.setattr(svc, 'get_user_by_id', AsyncMock(return_value=referrer))
    monkeypatch.setattr(svc, 'get_subscription_by_user_id', AsyncMock(return_value=referrer_subscription))
    monkeypatch.setattr(svc, 'create_reward_grant', AsyncMock(return_value=second_cycle_grant))
    extend_mock = AsyncMock(return_value=updated_subscription)
    monkeypatch.setattr(svc, 'extend_subscription', extend_mock)

    update_remnawave_user = AsyncMock()
    monkeypatch.setattr(
        'app.services.subscription_service.SubscriptionService',
        lambda: SimpleNamespace(update_remnawave_user=update_remnawave_user),
    )

    result = await svc.process_first_connected(db, user=user, subscription=subscription)

    assert result is True
    svc.get_last_reward_grant.assert_awaited_once_with(db, referrer_id=20)
    svc.create_reward_grant.assert_awaited_once_with(
        db,
        referrer_id=20,
        reward_cycle=2,
        qualified_count_at_grant=4,
        reward_days=7,
        subscription_id=40,
    )
    extend_mock.assert_awaited_once_with(db, referrer_subscription, 7)
    update_remnawave_user.assert_awaited_once_with(db, updated_subscription)


@pytest.mark.asyncio
async def test_first_connected_after_threshold_change_does_not_reuse_rewarded_referrals(monkeypatch):
    from app.services import referral_traffic_reward_service as svc

    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS', 1)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_DAYS', 7)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_QUALIFICATION_WINDOW_DAYS', 0)
    monkeypatch.setattr(svc.settings, 'REFERRAL_TRAFFIC_REWARD_NOTIFY', False)

    db = AsyncMock()
    user = SimpleNamespace(id=12, referred_by_id=20, remnawave_uuid='user-uuid-3', full_name='Referral 3')
    subscription = SimpleNamespace(id=32, remnawave_uuid='sub-uuid-3')
    referrer = SimpleNamespace(id=20, telegram_id=200, full_name='Referrer')
    referrer_subscription = SimpleNamespace(id=40)
    updated_subscription = SimpleNamespace(id=40)
    attribution = SimpleNamespace(mode='traffic_reward', referrer_id=20, mode_captured_at=None)
    previous_grant = SimpleNamespace(id=50, reward_cycle=1, qualified_count_at_grant=2)
    next_grant = SimpleNamespace(id=51, reward_cycle=2)

    monkeypatch.setattr(svc, 'get_referral_attribution', AsyncMock(return_value=attribution))
    monkeypatch.setattr(svc, 'create_qualification', AsyncMock(return_value=SimpleNamespace(id=62)))
    monkeypatch.setattr(svc, 'count_qualifications', AsyncMock(return_value=3))
    monkeypatch.setattr(svc, 'get_last_reward_grant', AsyncMock(return_value=previous_grant))
    monkeypatch.setattr(svc, 'get_user_by_id', AsyncMock(return_value=referrer))
    monkeypatch.setattr(svc, 'get_subscription_by_user_id', AsyncMock(return_value=referrer_subscription))
    monkeypatch.setattr(svc, 'create_reward_grant', AsyncMock(return_value=next_grant))
    extend_mock = AsyncMock(return_value=updated_subscription)
    monkeypatch.setattr(svc, 'extend_subscription', extend_mock)

    update_remnawave_user = AsyncMock()
    monkeypatch.setattr(
        'app.services.subscription_service.SubscriptionService',
        lambda: SimpleNamespace(update_remnawave_user=update_remnawave_user),
    )

    result = await svc.process_first_connected(db, user=user, subscription=subscription)

    assert result is True
    svc.create_reward_grant.assert_awaited_once_with(
        db,
        referrer_id=20,
        reward_cycle=2,
        qualified_count_at_grant=3,
        reward_days=7,
        subscription_id=40,
    )
    extend_mock.assert_awaited_once_with(db, referrer_subscription, 7)
    update_remnawave_user.assert_awaited_once_with(db, updated_subscription)


@pytest.mark.asyncio
async def test_referral_topup_skips_traffic_reward_attribution(monkeypatch):
    from app.services import referral_service

    db = AsyncMock()
    user = SimpleNamespace(id=10, referred_by_id=20)
    referrer = SimpleNamespace(id=20)

    monkeypatch.setattr(referral_service, 'get_user_by_id', AsyncMock(side_effect=[user, referrer]))
    monkeypatch.setattr(
        referral_service,
        'get_referral_attribution',
        AsyncMock(return_value=SimpleNamespace(mode='traffic_reward')),
    )
    create_mock = AsyncMock()
    monkeypatch.setattr(referral_service, 'create_referral_earning', create_mock)

    result = await referral_service.process_referral_topup(db, user_id=10, topup_amount_kopeks=10000)

    assert result is True
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_connected_webhook_calls_referral_reward_service(monkeypatch):
    from app.services.remnawave_webhook_service import RemnaWaveWebhookService

    service = RemnaWaveWebhookService.__new__(RemnaWaveWebhookService)
    service.bot = SimpleNamespace()
    service._get_subscription_keyboard = lambda user: None
    service._notify_user = AsyncMock()

    process_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.process_first_connected',
        process_mock,
    )

    db = AsyncMock()
    user = SimpleNamespace(id=10)
    subscription = SimpleNamespace(id=30)
    data = {'event': 'user.first_connected'}

    await service._handle_first_connected(db, user, subscription, data)

    process_mock.assert_awaited_once_with(db, user=user, subscription=subscription, data=data, bot=service.bot)
    service._notify_user.assert_awaited_once()


def test_remnawave_sync_first_connected_recovery_requires_first_connected_at(monkeypatch):
    from app.services.remnawave_service import RemnaWaveService

    service = RemnaWaveService.__new__(RemnaWaveService)
    monkeypatch.setattr('app.services.remnawave_service.settings.REFERRAL_TRAFFIC_REWARDS_ENABLED', True)

    candidates = []
    user = SimpleNamespace(id=10, referred_by_id=20)
    subscription = SimpleNamespace(id=30)

    service._append_first_connected_recovery_candidate(
        candidates,
        panel_user={'uuid': 'panel-uuid'},
        user=user,
        subscription=subscription,
    )

    assert candidates == []


@pytest.mark.asyncio
async def test_remnawave_sync_processes_first_connected_recovery(monkeypatch):
    from app.database.models import Subscription, User
    from app.services.remnawave_service import RemnaWaveService

    service = RemnaWaveService.__new__(RemnaWaveService)
    monkeypatch.setattr('app.services.remnawave_service.settings.REFERRAL_TRAFFIC_REWARDS_ENABLED', True)
    monkeypatch.setattr('app.services.remnawave_service.settings.REFERRAL_TRAFFIC_REWARD_NOTIFY', True)
    monkeypatch.setattr('app.services.remnawave_service.settings.BOT_TOKEN', 'test-token')

    candidates = []
    user = SimpleNamespace(id=10, referred_by_id=20)
    subscription = SimpleNamespace(id=30)
    panel_user = {'uuid': 'panel-uuid', 'firstConnectedAt': '2026-08-14T10:00:00+00:00'}

    service._append_first_connected_recovery_candidate(
        candidates,
        panel_user=panel_user,
        user=user,
        subscription=subscription,
    )

    assert candidates[0].user_id == 10
    assert candidates[0].subscription_id == 30

    process_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.process_first_connected',
        process_mock,
    )

    class FakeSession:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeBot:
        def __init__(self):
            self.session = FakeSession()

    fake_bot = FakeBot()
    monkeypatch.setattr('app.bot_factory.create_bot', lambda: fake_bot)

    recovery_db = AsyncMock()
    recovery_db.get = AsyncMock(side_effect=[user, subscription])

    class RecoverySession:
        async def __aenter__(self):
            return recovery_db

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr('app.database.database.AsyncSessionLocal', RecoverySession)

    sync_db = AsyncMock()
    processed = await service._process_first_connected_recovery_candidates(sync_db, candidates)

    assert processed == 1
    sync_db.get.assert_not_called()
    assert recovery_db.get.await_args_list[0].args == (User, 10)
    assert recovery_db.get.await_args_list[1].args == (Subscription, 30)
    process_mock.assert_awaited_once()
    _, kwargs = process_mock.call_args
    assert kwargs['user'] is user
    assert kwargs['subscription'] is subscription
    assert kwargs['bot'] is fake_bot
    assert fake_bot.session.closed is True
    assert kwargs['data'] == {
        'event': 'sync.first_connected_recovery',
        'uuid': 'panel-uuid',
        'firstConnectedAt': '2026-08-14T10:00:00+00:00',
    }
