import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated
from uuid import UUID

import httpx
from faststream import FastStream  # pyright: ignore[reportMissingImports]
from faststream.middlewares import AckPolicy  # pyright: ignore[reportMissingImports]
from faststream.params import NoCast  # pyright: ignore[reportMissingImports]
from faststream.rabbit.annotations import RabbitMessage  # pyright: ignore[reportMissingImports]
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.messaging import (
    CONSUMER_MAX_ATTEMPTS,
    CONSUMER_RETRY_BASE_DELAY_MS,
    HEADER_ATTEMPT,
    PAYMENTS_NEW_QUEUE,
    RABBIT_PAYMENTS_MAIN_EXCHANGE,
    RABBIT_PAYMENTS_MAIN_QUEUE,
    declare_payments_aux_infrastructure,
    payments_rabbit_broker,
    publish_to_dlq,
    publish_to_retry_queue,
)
from app.models.payment import Payment, PaymentStatus

logger = logging.getLogger(__name__)

WEBHOOK_MAX_ATTEMPTS = 3
# Экспоненциальная пауза между попытками webhook (ТЗ: 3 попытки): 1s, 2s
WEBHOOK_RETRY_DELAYS_SEC = tuple(1.0 * (2**i)
                                 for i in range(WEBHOOK_MAX_ATTEMPTS - 1))

broker = payments_rabbit_broker(consumer_prefetch=1)
app = FastStream(broker)


class InvalidPaymentMessage(Exception):
    """
    Невалидный JSON / нет payment_id / невалидный UUID.
    Политика: без ретраев, сразу в DLQ с кодом в args[0] (invalid_json, missing_payment_id, invalid_payment_id).
    """


class PaymentHandleResult(StrEnum):
    SUCCESS = "success"
    INVALID_PAYLOAD = "invalid_payload"
    TRANSIENT_FAILURE = "transient_failure"


@app.on_startup
async def _declare_payments_aux() -> None:
    await broker.connect()
    await declare_payments_aux_infrastructure(broker)


def _read_attempt(headers: dict | None) -> int:
    if not headers:
        return 1
    raw = headers.get(HEADER_ATTEMPT, 1)
    if isinstance(raw, int):
        return max(1, raw)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _parse_payment_id(body: bytes) -> UUID:
    try:
        data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidPaymentMessage("invalid_json") from exc
    try:
        pid = data["payment_id"]
    except (KeyError, TypeError) as exc:
        raise InvalidPaymentMessage("missing_payment_id") from exc
    try:
        return UUID(str(pid))
    except (ValueError, TypeError) as exc:
        raise InvalidPaymentMessage("invalid_payment_id") from exc


async def _send_webhook_with_retries(url: str, payload: dict) -> bool:
    async with httpx.AsyncClient() as client:
        for attempt in range(WEBHOOK_MAX_ATTEMPTS):
            try:
                response = await client.post(
                    str(url),
                    json=payload,
                    timeout=30.0,
                    headers={"Content-Type": "application/json"},
                )
                if response.is_success:
                    return True
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    logger.warning(
                        "Webhook client error %s for %s", response.status_code, url
                    )
                    return False
            except httpx.RequestError as exc:
                logger.warning(
                    "Webhook attempt %s/%s failed: %s",
                    attempt + 1,
                    WEBHOOK_MAX_ATTEMPTS,
                    exc,
                )
            if attempt < len(WEBHOOK_RETRY_DELAYS_SEC):
                await asyncio.sleep(WEBHOOK_RETRY_DELAYS_SEC[attempt])
    return False


async def _handle_payment_event(body: bytes) -> tuple[PaymentHandleResult, str | None]:
    try:
        payment_id = _parse_payment_id(body)
    except InvalidPaymentMessage as exc:
        code = str(exc.args[0]) if exc.args else "invalid_payload"
        logger.error("Invalid payment message → DLQ (reason=%s)", code)
        return PaymentHandleResult.INVALID_PAYLOAD, code

    await asyncio.sleep(random.uniform(2.0, 5.0))
    processing_ok = random.random() < 0.9
    new_status = (
        PaymentStatus.SUCCEEDED if processing_ok else PaymentStatus.FAILED
    )

    async with async_session_maker() as db:
        result = await db.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()
        if payment is None:
            logger.warning(
                "Payment not found %s — считаем временным сбоем для ретраев",
                payment_id,
            )
            return PaymentHandleResult.TRANSIENT_FAILURE, None

        if payment.status != PaymentStatus.PENDING:
            logger.info(
                "Skip duplicate/out-of-order message for payment %s (status=%s)",
                payment_id,
                payment.status,
            )
            return PaymentHandleResult.SUCCESS, None

        payment.status = new_status
        payment.processed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(payment)

        webhook_url = payment.webhook_url
        amount = float(payment.amount)

    if not webhook_url:
        return PaymentHandleResult.SUCCESS, None

    payload = {
        "payment_id": str(payment_id),
        "status": new_status.value,
        "amount": amount,
    }
    ok = await _send_webhook_with_retries(webhook_url, payload)
    if not ok:
        logger.error(
            "Webhook delivery failed after retries for payment %s", payment_id
        )
    return PaymentHandleResult.SUCCESS, None


@broker.subscriber(
    RABBIT_PAYMENTS_MAIN_QUEUE,
    RABBIT_PAYMENTS_MAIN_EXCHANGE,
    ack_policy=AckPolicy.MANUAL,
)
async def process_payment_message(
    body: Annotated[bytes, NoCast],
    message: RabbitMessage,
) -> None:
    attempt = _read_attempt(message.headers)
    try:
        result, invalid_detail = await _handle_payment_event(body)
    except Exception:
        logger.exception(
            "Unexpected error consuming message (attempt %s)", attempt
        )
        result, invalid_detail = PaymentHandleResult.TRANSIENT_FAILURE, None

    try:
        if result is PaymentHandleResult.INVALID_PAYLOAD:
            await publish_to_dlq(
                broker,
                body,
                failure_reason=invalid_detail or "invalid_payload",
            )
            await message.ack()
            return

        if result is PaymentHandleResult.SUCCESS:
            await message.ack()
            return

        if attempt >= CONSUMER_MAX_ATTEMPTS:
            await publish_to_dlq(
                broker,
                body,
                failure_reason="max_retries_exceeded",
                final_attempt=attempt,
            )
            await message.ack()
            return

        delay_ms = CONSUMER_RETRY_BASE_DELAY_MS * (2 ** (attempt - 1))
        await publish_to_retry_queue(
            broker,
            body,
            next_attempt=attempt + 1,
            delay_ms=delay_ms,
        )
        await message.ack()
    except Exception:
        logger.exception(
            "Failed to ack/republish; возвращаем в очередь (nack requeue=True)"
        )
        await message.nack(requeue=True)


async def main() -> None:
    logging.basicConfig(level=settings.log_level)
    logger.info("Waiting for messages on queue %s", PAYMENTS_NEW_QUEUE)
    await app.run()


if __name__ == "__main__":
    asyncio.run(main())
