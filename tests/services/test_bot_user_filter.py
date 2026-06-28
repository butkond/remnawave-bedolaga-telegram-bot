import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import _compile_username_template_regex, settings


def test_template_regex_default_matches_only_template_usernames():
    regex = _compile_username_template_regex('user_{telegram_id}')

    assert regex.fullmatch('user_123')
    assert regex.fullmatch('user_999999999')
    assert not regex.fullmatch('user_abc')  # telegram_id должен быть числом
    assert not regex.fullmatch('bob_123')  # другой префикс
    assert not regex.fullmatch('xuser_123')  # лишний префикс


def test_template_regex_with_username_placeholder():
    regex = _compile_username_template_regex('{username}_{telegram_id}')

    assert regex.fullmatch('john_123')
    assert regex.fullmatch('john_doe_123')
    assert not regex.fullmatch('john_abc')  # хвост не число


def test_template_regex_with_separator():
    regex = _compile_username_template_regex('vpn-{telegram_id}')

    assert regex.fullmatch('vpn-555')
    assert not regex.fullmatch('vpn-555x')
    assert not regex.fullmatch('vpn_555')


def test_settings_username_matches_template_default():
    # Дефолтный шаблон REMNAWAVE_USER_USERNAME_TEMPLATE = 'user_{telegram_id}'
    assert settings.username_matches_template('user_42')
    assert settings.username_matches_template('@user_42')  # @ срезается
    assert not settings.username_matches_template('foreign_user')
    assert not settings.username_matches_template('')
    assert not settings.username_matches_template(None)


async def test_bulk_extend_subscriptions_counts(monkeypatch):
    from app.services.subscription_service import SubscriptionService

    service = SubscriptionService.__new__(SubscriptionService)

    subs = [SimpleNamespace(id=1), SimpleNamespace(id=2), SimpleNamespace(id=3)]
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = subs
    db.execute = AsyncMock(return_value=result)

    # Вторая подписка падает на этапе продления в БД -> errors += 1
    extend_mock = AsyncMock(side_effect=[None, Exception('boom'), None])
    monkeypatch.setattr('app.database.crud.subscription.extend_subscription', extend_mock)
    service.update_remnawave_user = AsyncMock()

    stats = await service.bulk_extend_subscriptions(db, 30, admin_id=1)

    assert stats == {'total': 3, 'ok': 2, 'errors': 1}
    assert extend_mock.await_count == 3
    # Панель синхронизируется только для успешно продлённых подписок
    assert service.update_remnawave_user.await_count == 2
