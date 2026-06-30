import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.remnawave_service import RemnaWaveService


def _create_service() -> RemnaWaveService:
    service = RemnaWaveService.__new__(RemnaWaveService)
    service._panel_timezone = ZoneInfo('UTC')
    service._utc_timezone = ZoneInfo('UTC')
    return service


def _make_panel_user(uuid: str, expire_at: str, status: str = 'ACTIVE', telegram_id: int | None = None) -> dict:
    return {
        'uuid': uuid,
        'telegramId': telegram_id,
        'expireAt': expire_at,
        'status': status,
    }


def _async_nested_ctx() -> MagicMock:
    """Async context manager for ``db.begin_nested()`` (SAVEPOINT)."""
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=None)
    return nested


def test_deduplicate_prefers_latest_expire_date():
    service = _create_service()

    uuid = 'uuid-1'
    older = _make_panel_user(uuid, datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat())
    newer = _make_panel_user(uuid, datetime(2025, 2, 1, 0, 0, 0, tzinfo=UTC).isoformat())

    deduplicated = service._deduplicate_panel_users_by_telegram_id([older, newer])

    assert deduplicated[uuid] is newer


def test_deduplicate_prefers_active_status_on_same_expire():
    service = _create_service()

    uuid = 'uuid-2'
    expire = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat()
    disabled = _make_panel_user(uuid, expire, status='DISABLED')
    active = _make_panel_user(uuid, expire, status='ACTIVE')

    deduplicated = service._deduplicate_panel_users_by_telegram_id([disabled, active])

    assert deduplicated[uuid] is active


def test_deduplicate_skips_users_without_uuid():
    service = _create_service()

    without_uuid = _make_panel_user('', datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC).isoformat())
    with_uuid = _make_panel_user('uuid-3', datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC).isoformat())

    deduplicated = service._deduplicate_panel_users_by_telegram_id([without_uuid, with_uuid])

    assert list(deduplicated) == ['uuid-3']
    assert deduplicated['uuid-3'] is with_uuid


async def test_get_or_create_user_handles_unique_violation(monkeypatch):
    service = _create_service()
    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=_async_nested_ctx())

    panel_user = {'telegramId': 555, 'username': 'existing'}
    existing_user = object()

    create_user_mock = AsyncMock(side_effect=IntegrityError('stmt', 'params', Exception('unique')))
    get_user_mock = AsyncMock(return_value=existing_user)

    monkeypatch.setattr('app.services.remnawave_service.create_user_no_commit', create_user_mock)
    monkeypatch.setattr(
        'app.services.remnawave_service.get_user_by_telegram_id',
        get_user_mock,
    )

    user, created = await service._get_or_create_bot_user_from_panel(db, panel_user)

    assert user is existing_user
    assert created is False
    create_user_mock.assert_awaited_once()
    get_user_mock.assert_awaited_once_with(db, 555)


async def test_get_or_create_user_creates_new(monkeypatch):
    service = _create_service()
    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=_async_nested_ctx())

    panel_user = {'telegramId': 777, 'username': 'new_user'}
    new_user = object()

    create_user_mock = AsyncMock(return_value=new_user)

    monkeypatch.setattr('app.services.remnawave_service.create_user_no_commit', create_user_mock)

    user, created = await service._get_or_create_bot_user_from_panel(db, panel_user)

    assert user is new_user
    assert created is True
    create_user_mock.assert_awaited_once_with(
        db=db,
        telegram_id=777,
        username='new_user',
        first_name='User 777',
        last_name=None,
        language='ru',
    )


async def test_get_or_create_user_requires_telegram_id():
    """Записи панели без telegram_id не создают пользователя бота."""
    service = _create_service()
    db = AsyncMock()

    user, created = await service._get_or_create_bot_user_from_panel(db, {'telegramId': None, 'email': 'x@y.z'})

    assert user is None
    assert created is False
