import hashlib
import hmac
import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import structlog

from app.config import settings


logger = structlog.get_logger(__name__)


class TributeService:
    def __init__(self):
        self.api_key = settings.TRIBUTE_API_KEY
        self.donate_link = settings.TRIBUTE_DONATE_LINK

    async def create_payment_link(
        self, user_id: int, amount_kopeks: int = 0, description: str = 'Пополнение баланса'
    ) -> str | None:
        if not settings.TRIBUTE_ENABLED:
            logger.warning('Tribute платежи отключены')
            return None

        try:
            if not self.donate_link:
                logger.error('Tribute donate link не настроен')
                return None

            payment_url = self._append_query_param(self.donate_link, 'user_id', str(user_id))

            logger.info('Создана ссылка Tribute для пользователя', user_id=user_id)
            return payment_url

        except Exception as e:
            logger.error('Ошибка создания Tribute ссылки', error=e)
            return None

    def verify_webhook_signature(self, payload: str, signature: str) -> bool:
        if not self.api_key:
            logger.error('Tribute API key не настроен — отклоняем webhook')
            return False

        try:
            expected_signature = hmac.new(self.api_key.encode(), payload.encode(), hashlib.sha256).hexdigest()

            is_valid = hmac.compare_digest(signature, expected_signature)

            if is_valid:
                logger.info('✅ Подпись Tribute webhook проверена успешно')
            else:
                logger.error('❌ Неверная подпись Tribute webhook')

            return is_valid

        except Exception as e:
            logger.error('Ошибка проверки подписи webhook', error=e)
            return False

    async def process_webhook(self, payload_or_data) -> dict[str, Any] | None:
        try:
            logger.info('🔄 Начинаем обработку Tribute webhook')

            if isinstance(payload_or_data, str):
                try:
                    webhook_data = json.loads(payload_or_data)
                    logger.info('📊 Распарсенные данные', webhook_data=webhook_data)
                except json.JSONDecodeError as e:
                    logger.error('❌ Ошибка парсинга JSON', error=e)
                    return None
            else:
                webhook_data = payload_or_data

            payment_id = None
            status = None
            amount_kopeks = 0
            telegram_user_id = None
            trb_user_id = None

            payment_id = webhook_data.get('id') or webhook_data.get('payment_id')
            status = webhook_data.get('status')
            amount_kopeks = webhook_data.get('amount', 0)
            telegram_user_id = webhook_data.get('telegram_user_id')
            trb_user_id = webhook_data.get('trb_user_id')

            if not payment_id and 'payload' in webhook_data and 'name' not in webhook_data:
                data = webhook_data['payload']
                payment_id = data.get('id') or data.get('payment_id')
                status = data.get('status')
                amount_kopeks = data.get('amount', 0)
                telegram_user_id = data.get('telegram_user_id')
                trb_user_id = data.get('trb_user_id')

            if 'name' in webhook_data:
                event_name = webhook_data.get('name')
                data = webhook_data.get('payload', {})
                amount_kopeks = data.get('amount', 0)
                telegram_user_id = data.get('telegram_user_id')
                trb_user_id = data.get('trb_user_id')

                if event_name == 'new_digital_product':
                    product_id = data.get('product_id')
                    configured_product = settings.get_tribute_digital_product(product_id)
                    if not settings.is_tribute_digital_product_mode() or not configured_product:
                        logger.warning(
                            'Tribute digital product webhook ignored: product is not configured',
                            product_id=product_id,
                        )
                        return None

                    amount_kopeks = self._parse_digital_product_amount_kopeks(data.get('amount'), data.get('currency'))
                    if amount_kopeks is None:
                        logger.warning(
                            'Tribute digital product webhook ignored: unsupported currency or amount',
                            product_id=product_id,
                            amount=data.get('amount'),
                            currency=data.get('currency'),
                        )
                        return None

                    purchase_id = data.get('purchase_id') or data.get('id') or data.get('payment_id')
                    created_at = webhook_data.get('created_at')
                    if purchase_id is not None:
                        payment_id = f'digital_product_{purchase_id}'
                    elif product_id is not None and created_at:
                        payment_id = f'digital_product_{product_id}_{telegram_user_id}_{amount_kopeks}_{created_at}'
                    else:
                        payment_id = None
                    status = 'paid'
                elif event_name == 'digital_product_refunded':
                    product_id = data.get('product_id')
                    purchase_id = data.get('purchase_id') or data.get('id') or data.get('payment_id')
                    configured_product = settings.get_tribute_digital_product(product_id)
                    if not settings.is_tribute_digital_product_mode() or not configured_product:
                        logger.warning(
                            'Tribute digital product refund ignored: product is not configured',
                            product_id=product_id,
                        )
                        return None

                    amount_kopeks = self._parse_digital_product_amount_kopeks(data.get('amount'), data.get('currency'))
                    if amount_kopeks is None:
                        logger.warning(
                            'Tribute digital product refund ignored: unsupported currency or amount',
                            product_id=product_id,
                            amount=data.get('amount'),
                            currency=data.get('currency'),
                        )
                        return None

                    payment_id = f'digital_product_{purchase_id}' if purchase_id is not None else None
                    status = 'refunded'
                elif event_name in ('new_donation', 'recurrent_donation'):
                    if settings.is_tribute_digital_product_mode():
                        logger.warning('Tribute donation webhook ignored in digital product mode')
                        return None
                    # `donation_request_id` is the donate request/link id, not a
                    # unique payment id. Reusing it would mark every next donation
                    # through the same Tribute link as a duplicate. The envelope
                    # `created_at` is stable for delivery retries and differs for
                    # separate donations, so it is part of the idempotency key.
                    donation_request_id = data.get('donation_request_id')
                    created_at = webhook_data.get('created_at')
                    if donation_request_id is not None and created_at:
                        payment_id = f'{donation_request_id}_{telegram_user_id}_{amount_kopeks}_{created_at}'
                    elif donation_request_id is not None:
                        payment_id = None
                    status = 'paid'
                elif event_name == 'cancelled_subscription':
                    status = 'cancelled'
                else:
                    status = 'unknown'

            logger.info(
                '📝 Извлеченные данные',
                payment_id=payment_id,
                status=status,
                amount_kopeks=amount_kopeks,
                telegram_user_id=telegram_user_id,
                trb_user_id=trb_user_id,
            )

            if not telegram_user_id:
                logger.error(
                    '❌ Не найден telegram_user_id в webhook данных',
                    trb_user_id=trb_user_id,
                )
                logger.error(
                    '🔍 Полные данные для отладки', dumps=json.dumps(webhook_data, ensure_ascii=False, indent=2)
                )
                return None

            try:
                telegram_user_id = int(telegram_user_id)
            except (ValueError, TypeError):
                logger.error('❌ Некорректный telegram_user_id', telegram_user_id=telegram_user_id)
                return None

            result = {
                'event_type': 'refund' if status == 'refunded' else 'payment',
                'payment_id': payment_id or f'tribute_{telegram_user_id}_{amount_kopeks}',
                'user_id': telegram_user_id,
                'trb_user_id': trb_user_id,
                'amount_kopeks': int(amount_kopeks) if amount_kopeks else 0,
                'status': status or 'paid',
                'external_id': f'donation_{payment_id or "unknown"}',
                'payment_system': 'tribute',
            }

            logger.info('✅ Tribute webhook обработан успешно', result=result)
            return result

        except Exception as e:
            logger.error('❌ Ошибка обработки Tribute webhook', error=e, exc_info=True)
            logger.error('🔍 Webhook data для отладки', dumps=json.dumps(webhook_data, ensure_ascii=False, indent=2))
            return None

    @staticmethod
    def _append_query_param(url: str, key: str, value: str) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query[key] = value
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    @staticmethod
    def _parse_digital_product_amount_kopeks(amount: Any, currency: Any) -> int | None:
        if str(currency or '').strip().lower() != 'rub':
            return None

        try:
            amount_rubles = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            return None

        if amount_rubles <= 0:
            return None

        return int((amount_rubles * Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))

    async def get_payment_status(self, payment_id: str) -> dict[str, Any] | None:
        try:
            logger.info('Запрос статуса платежа', payment_id=payment_id)
            return {'status': 'unknown', 'payment_id': payment_id}
        except Exception as e:
            logger.error('Ошибка получения статуса платежа', error=e)
            return None

    async def refund_payment(
        self, payment_id: str, amount_kopeks: int | None = None, reason: str = 'Возврат по запросу'
    ) -> dict[str, Any] | None:
        try:
            logger.info('Создание возврата для платежа', payment_id=payment_id)
            return {'refund_id': f'refund_{payment_id}', 'status': 'pending'}
        except Exception as e:
            logger.error('Ошибка создания возврата', error=e)
            return None
