import pytest
import structlog

from app.config import settings
from app.services.platega_service import PlategaService


def test_sanitize_description_limits_utf8_bytes() -> None:
    original = 'Интернет-сервис - Пополнение баланса на 50 ₽ и ещё чуть-чуть'

    with structlog.testing.capture_logs() as logs:
        trimmed = PlategaService._sanitize_description(original, 64)

    assert len(trimmed.encode('utf-8')) <= 64
    assert trimmed != original
    assert any('trimmed' in entry.get('event', '') for entry in logs)


def test_sanitize_description_returns_clean_value() -> None:
    original = '  Обычное описание  '

    trimmed = PlategaService._sanitize_description(original, 64)

    assert trimmed == 'Обычное описание'
    assert len(trimmed.encode('utf-8')) <= 64


@pytest.mark.anyio('asyncio')
async def test_create_payment_uses_v2_endpoint_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'PLATEGA_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_MERCHANT_ID', 'merchant', raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_SECRET', 'secret', raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_BASE_URL', 'https://app.platega.io', raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_API_VERSION', 'v2', raising=False)

    service = PlategaService()
    captured: dict[str, str] = {}

    async def fake_request(method: str, endpoint: str, **_: object) -> dict[str, str]:
        captured['method'] = method
        captured['endpoint'] = endpoint
        return {'transactionId': 'tx', 'url': 'https://pay.platega.io'}

    monkeypatch.setattr(service, '_request', fake_request)

    await service.create_payment(payment_method=11, amount=100, currency='RUB')

    assert service.api_version == 'v2'
    assert captured == {'method': 'POST', 'endpoint': '/v2/transaction/process'}


def test_base_url_version_suffix_forces_version_and_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'PLATEGA_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_MERCHANT_ID', 'merchant', raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_SECRET', 'secret', raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_BASE_URL', 'https://app.platega.io/V2', raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_API_VERSION', 'v1', raising=False)

    service = PlategaService()

    assert service.base_url == 'https://app.platega.io'
    assert service.api_version == 'v2'


def test_parse_redirect_url_accepts_v1_and_v2_fields() -> None:
    assert PlategaService.parse_redirect_url({'redirect': 'https://pay.platega.io/v1'}) == 'https://pay.platega.io/v1'
    assert PlategaService.parse_redirect_url({'url': 'https://pay.platega.io/v2'}) == 'https://pay.platega.io/v2'
    assert PlategaService.parse_redirect_url({}) is None
