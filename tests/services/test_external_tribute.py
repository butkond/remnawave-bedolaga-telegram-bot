from copy import deepcopy

import pytest

from app.external.tribute import TributeService


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


@pytest.mark.anyio('asyncio')
async def test_new_donation_payment_id_uses_event_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('app.external.tribute.settings.TRIBUTE_API_KEY', 'secret', raising=False)
    monkeypatch.setattr('app.external.tribute.settings.TRIBUTE_DONATE_LINK', 'https://t.me/tribute/app', raising=False)
    monkeypatch.setattr('app.external.tribute.settings.TRIBUTE_MODE', 'donation', raising=False)

    service = TributeService()
    webhook = {
        'created_at': '2026-08-30T17:04:26.048782Z',
        'name': 'new_donation',
        'payload': {
            'donation_request_id': 198388,
            'amount': 20000,
            'currency': 'rub',
            'telegram_user_id': 684047866,
            'trb_user_id': 'T-19520283',
        },
    }
    next_webhook = deepcopy(webhook)
    next_webhook['created_at'] = '2026-08-30T17:10:55.608865Z'

    first = await service.process_webhook(webhook)
    replay = await service.process_webhook(deepcopy(webhook))
    second = await service.process_webhook(next_webhook)

    assert first is not None
    assert replay is not None
    assert second is not None
    assert first['payment_id'] == replay['payment_id']
    assert second['payment_id'] != first['payment_id']
    assert str(first['payment_id']).startswith('198388_684047866_20000_')


@pytest.mark.anyio('asyncio')
async def test_new_digital_product_uses_purchase_id_and_webhook_amount_kopeks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr('app.external.tribute.settings.TRIBUTE_MODE', 'digital_product', raising=False)
    monkeypatch.setattr(
        'app.external.tribute.settings.TRIBUTE_DIGITAL_PRODUCTS',
        '456|https://t.me/tribute/app?startapp=p456|7 дней',
        raising=False,
    )

    service = TributeService()

    result = await service.process_webhook(
        {
            'name': 'new_digital_product',
            'created_at': '2026-09-02T12:00:00Z',
            'payload': {
                'purchase_id': 12345,
                'product_id': 456,
                'amount': 10000,
                'currency': 'rub',
                'telegram_user_id': 684047866,
                'trb_user_id': 'T-19520283',
            },
        }
    )

    assert result is not None
    assert result['event_type'] == 'payment'
    assert result['status'] == 'paid'
    assert result['payment_id'] == 'digital_product_12345'
    assert result['amount_kopeks'] == 10000
    assert result['user_id'] == 684047866


@pytest.mark.anyio('asyncio')
async def test_new_digital_product_ignores_non_rub_currency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('app.external.tribute.settings.TRIBUTE_MODE', 'digital_product', raising=False)
    monkeypatch.setattr(
        'app.external.tribute.settings.TRIBUTE_DIGITAL_PRODUCTS',
        '456|https://t.me/tribute/app?startapp=p456|7 дней',
        raising=False,
    )

    service = TributeService()

    result = await service.process_webhook(
        {
            'name': 'new_digital_product',
            'created_at': '2026-09-02T12:00:00Z',
            'payload': {
                'purchase_id': 12345,
                'product_id': 456,
                'amount': 10000,
                'currency': 'usd',
                'telegram_user_id': 684047866,
            },
        }
    )

    assert result is None


@pytest.mark.anyio('asyncio')
async def test_new_digital_product_ignores_unconfigured_product(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('app.external.tribute.settings.TRIBUTE_MODE', 'digital_product', raising=False)
    monkeypatch.setattr(
        'app.external.tribute.settings.TRIBUTE_DIGITAL_PRODUCTS',
        '456|https://t.me/tribute/app?startapp=p456|7 дней',
        raising=False,
    )

    service = TributeService()

    result = await service.process_webhook(
        {
            'name': 'new_digital_product',
            'created_at': '2026-09-02T12:00:00Z',
            'payload': {
                'purchase_id': 12345,
                'product_id': 999,
                'amount': 50000,
                'telegram_user_id': 684047866,
            },
        }
    )

    assert result is None


@pytest.mark.anyio('asyncio')
async def test_digital_product_refund_uses_purchase_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('app.external.tribute.settings.TRIBUTE_MODE', 'digital_product', raising=False)
    monkeypatch.setattr(
        'app.external.tribute.settings.TRIBUTE_DIGITAL_PRODUCTS',
        '456|https://t.me/tribute/app?startapp=p456|7 дней',
        raising=False,
    )

    service = TributeService()

    result = await service.process_webhook(
        {
            'name': 'digital_product_refunded',
            'created_at': '2026-09-02T12:00:00Z',
            'payload': {
                'purchase_id': 12345,
                'product_id': 456,
                'amount': 10000,
                'currency': 'rub',
                'telegram_user_id': 684047866,
            },
        }
    )

    assert result is not None
    assert result['event_type'] == 'refund'
    assert result['payment_id'] == 'digital_product_12345'
    assert result['amount_kopeks'] == 10000
