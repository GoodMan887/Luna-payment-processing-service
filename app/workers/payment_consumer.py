import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from uuid import UUID

import aio_pika
import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.messaging import PAYMENTS_NEW_QUEUE
from app.models.payment import Payment, PaymentStatus

logger = logging.getLogger(__name__)

WEBHOOK_MAX_ATTEMPTS = 5
WEBHOOK_RETRY_DELAYS_SEC = (1.0, 2.0, 4.0, 8.0)


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


async def _handle_payment_event(body: bytes) -> None:
    try:
        data = json.loads(body.decode("utf-8"))
        payment_id = UUID(data["payment_id"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.error("Invalid message payload: %s", exc)
        return

    await asyncio.sleep(random.uniform(2.0, 5.0))
    processing_ok = random.random() < 0.9
    new_status = (
        PaymentStatus.SUCCEEDED if processing_ok else PaymentStatus.FAILED
    )

    async with async_session_maker() as db:
        result = await db.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()
        if payment is None:
            logger.error("Payment not found: %s", payment_id)
            return

        if payment.status != PaymentStatus.PENDING:
            logger.info(
                "Skip duplicate/out-of-order message for payment %s (status=%s)",
                payment_id,
                payment.status,
            )
            return

        payment.status = new_status
        payment.processed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(payment)

        webhook_url = payment.webhook_url
        amount = float(payment.amount)

    if not webhook_url:
        return

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


async def main() -> None:
    logging.basicConfig(level=settings.log_level)
    connection = await aio_pika.connect_robust(str(settings.rabbitmq_url))
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue(PAYMENTS_NEW_QUEUE, durable=True)

        async def process_message(message: aio_pika.IncomingMessage) -> None:
            async with message.process():
                await _handle_payment_event(message.body)

        logger.info("Waiting for messages on queue %s", PAYMENTS_NEW_QUEUE)
        await queue.consume(process_message)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
