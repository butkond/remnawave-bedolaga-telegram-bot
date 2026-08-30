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
