from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.database.crud import subscription as subscription_crud
from app.keyboards.inline import get_referral_keyboard
from app.services.referral_traffic_reward_service import referral_traffic_reward_service


def _load_referral_handler():
    import importlib.util
    from pathlib import Path

    module_path = Path(__file__).resolve().parents[2] / 'app' / 'handlers' / 'referral.py'
    spec = importlib.util.spec_from_file_location('tests.referral_handler_for_test', module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.anyio
async def test_update_subscription_usage_triggers_traffic_reward(monkeypatch: pytest.MonkeyPatch) -> None:
    subscription = SimpleNamespace(id=1, user_id=10, traffic_used_gb=5.0, remnawave_uuid='sub-uuid')
    process_mock = AsyncMock(return_value=SimpleNamespace(qualified=True, granted=False))
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.referral_traffic_reward_service.process_subscription_traffic_update',
        process_mock,
    )

    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())

    await subscription_crud.update_subscription_usage(db, subscription, 12.0, bot=SimpleNamespace())

    process_mock.assert_awaited_once()
    call = process_mock.await_args
    assert call.args[2] == 5.0
    assert call.args[3] == 12.0


def test_referral_reward_modes_support_explicit_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'REFERRAL_PROGRAM_ENABLED', False, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_BALANCE_COMMISSION_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True, raising=False)

    assert referral_traffic_reward_service.get_available_modes() == []
    assert referral_traffic_reward_service.get_default_mode() == 'balance_commission'
    assert referral_traffic_reward_service.has_mode_selection() is False

    monkeypatch.setattr(settings, 'REFERRAL_PROGRAM_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_BALANCE_COMMISSION_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', False, raising=False)

    assert referral_traffic_reward_service.get_available_modes() == ['balance_commission']
    assert referral_traffic_reward_service.get_default_mode() == 'balance_commission'
    assert referral_traffic_reward_service.has_mode_selection() is False

    monkeypatch.setattr(settings, 'REFERRAL_BALANCE_COMMISSION_ENABLED', False, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True, raising=False)

    assert referral_traffic_reward_service.get_available_modes() == ['traffic_reward']
    assert referral_traffic_reward_service.get_default_mode() == 'traffic_reward'
    assert referral_traffic_reward_service.has_mode_selection() is False

    monkeypatch.setattr(settings, 'REFERRAL_BALANCE_COMMISSION_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True, raising=False)

    assert referral_traffic_reward_service.get_available_modes() == ['balance_commission', 'traffic_reward']
    assert referral_traffic_reward_service.has_mode_selection() is True


@pytest.mark.anyio
async def test_process_subscription_traffic_update_grants_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'REFERRAL_BALANCE_COMMISSION_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS', 3, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARD_THRESHOLD_GB', 10, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARD_DAYS', 7, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARD_NOTIFY', False, raising=False)

    user = SimpleNamespace(
        id=10,
        referred_by_id=20,
        created_at=None,
        remnawave_uuid='user-uuid',
    )
    referrer = SimpleNamespace(
        id=20,
        telegram_id=200,
        language='ru',
        referral_reward_mode='traffic_reward',
        referral_reward_mode_updated_at=None,
    )
    subscription = SimpleNamespace(id=30, user_id=10, remnawave_uuid='sub-uuid', traffic_used_gb=12.0)
    reward_subscription = SimpleNamespace(id=40, user_id=20, remnawave_uuid='ref-sub-uuid', traffic_used_gb=1.0)

    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.get_user_by_id',
        AsyncMock(side_effect=lambda db, user_id: user if user_id == 10 else referrer if user_id == 20 else None),
    )
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.get_referral_attribution_by_referral_id',
        AsyncMock(return_value=SimpleNamespace(mode='traffic_reward')),
    )
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.get_referral_traffic_qualification_by_referral_id',
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.create_referral_traffic_qualification',
        AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.count_referrer_traffic_qualifications',
        AsyncMock(return_value=3),
    )
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.get_referral_traffic_reward_grant_by_referrer_id',
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.get_subscription_by_user_id',
        AsyncMock(return_value=reward_subscription),
    )
    extend_mock = AsyncMock()
    monkeypatch.setattr('app.services.referral_traffic_reward_service.extend_subscription', extend_mock)
    create_grant_mock = AsyncMock(return_value=SimpleNamespace(id=2))
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.create_referral_traffic_reward_grant',
        create_grant_mock,
    )

    reward_sync_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        'app.services.subscription_service.SubscriptionService',
        lambda: SimpleNamespace(update_remnawave_user=reward_sync_mock),
    )

    result = await referral_traffic_reward_service.process_subscription_traffic_update(
        SimpleNamespace(),
        subscription,
        5.0,
        12.0,
    )

    assert result.qualified is True
    assert result.granted is True
    extend_mock.assert_awaited_once()
    create_grant_mock.assert_awaited_once()
    reward_sync_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_process_subscription_traffic_update_skips_when_already_qualified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, 'REFERRAL_BALANCE_COMMISSION_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARD_THRESHOLD_GB', 10, raising=False)

    user = SimpleNamespace(id=10, referred_by_id=20, created_at=None, remnawave_uuid='user-uuid')
    subscription = SimpleNamespace(id=30, user_id=10, remnawave_uuid='sub-uuid', traffic_used_gb=12.0)

    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.get_user_by_id',
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.get_referral_attribution_by_referral_id',
        AsyncMock(return_value=SimpleNamespace(mode='traffic_reward')),
    )
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.get_referral_traffic_qualification_by_referral_id',
        AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    create_qualification_mock = AsyncMock()
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.create_referral_traffic_qualification',
        create_qualification_mock,
    )

    result = await referral_traffic_reward_service.process_subscription_traffic_update(
        SimpleNamespace(),
        subscription,
        5.0,
        12.0,
    )

    assert result.qualified is True
    assert result.granted is False
    create_qualification_mock.assert_not_awaited()


def test_referral_keyboard_switch_button_visibility(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'REFERRAL_BALANCE_COMMISSION_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', False, raising=False)

    keyboard = get_referral_keyboard('ru')
    assert all(button.callback_data != 'referral_mode_switch' for row in keyboard.inline_keyboard for button in row)


def test_referral_keyboard_shows_switch_button_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'REFERRAL_BALANCE_COMMISSION_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True, raising=False)

    keyboard = get_referral_keyboard('ru', 'traffic_reward')
    assert any(button.callback_data == 'referral_mode_switch' for row in keyboard.inline_keyboard for button in row)


def test_referral_keyboard_traffic_only_hides_switch_button(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'REFERRAL_BALANCE_COMMISSION_ENABLED', False, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True, raising=False)

    keyboard = get_referral_keyboard('ru')
    assert all(button.callback_data != 'referral_mode_switch' for row in keyboard.inline_keyboard for button in row)


def test_default_mode_does_not_start_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'REFERRAL_BALANCE_COMMISSION_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True, raising=False)

    referrer = SimpleNamespace(referral_reward_mode='balance_commission', referral_reward_mode_updated_at=None)
    can_switch, remaining_hours = referral_traffic_reward_service.can_switch_referrer_mode(referrer)

    assert can_switch is True
    assert remaining_hours == 0


@pytest.mark.anyio
async def test_capture_referral_attribution_stores_referrer_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'REFERRAL_BALANCE_COMMISSION_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True, raising=False)

    referral = SimpleNamespace(id=11, referral_code='referral-own-code')
    referrer = SimpleNamespace(
        id=22,
        referral_code='referrer-applied-code',
        referral_reward_mode='balance_commission',
        referral_reward_mode_updated_at=None,
    )
    create_mock = AsyncMock(return_value=SimpleNamespace(id=1))
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.create_referral_attribution',
        create_mock,
    )
    monkeypatch.setattr(
        'app.services.referral_traffic_reward_service.get_referral_attribution_by_referral_id',
        AsyncMock(return_value=None),
    )

    db = SimpleNamespace()
    await referral_traffic_reward_service.capture_referral_attribution(db, referral=referral, referrer=referrer)

    assert create_mock.await_args.kwargs['referral_code'] == 'referrer-applied-code'
    assert getattr(referrer, 'referral_reward_mode_updated_at', None) is None


@pytest.mark.anyio
async def test_switch_referral_mode_does_not_fall_through_to_qr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'REFERRAL_BALANCE_COMMISSION_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'REFERRAL_TRAFFIC_REWARDS_ENABLED', True, raising=False)

    db_user = SimpleNamespace(
        id=11,
        language='ru',
        referral_code='ref-code',
        referral_reward_mode='balance_commission',
        referral_reward_mode_updated_at=None,
    )
    callback = SimpleNamespace(answer=AsyncMock(), bot=SimpleNamespace())
    db = SimpleNamespace()
    referral_handler = _load_referral_handler()

    set_mode_mock = AsyncMock(return_value='traffic_reward')
    monkeypatch.setattr(
        referral_handler.referral_traffic_reward_service,
        'set_referrer_mode',
        set_mode_mock,
    )
    monkeypatch.setattr(
        referral_handler.referral_traffic_reward_service,
        'can_switch_referrer_mode',
        lambda _referrer: (True, 0),
    )
    show_info_mock = AsyncMock()
    monkeypatch.setattr(referral_handler, 'show_referral_info', show_info_mock)
    monkeypatch.setattr(
        referral_handler.qrcode,
        'make',
        lambda *_args, **_kwargs: pytest.fail('QR generation must not run when switching mode'),
    )

    await referral_handler.switch_referral_mode(callback, db_user, db)

    set_mode_mock.assert_awaited_once_with(db, db_user, 'traffic_reward')
    show_info_mock.assert_awaited_once_with(callback, db_user, db)
