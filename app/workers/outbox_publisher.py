import asyncio
import json
import logging
from typing import Any

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.messaging import (
    PAYMENTS_NEW_QUEUE,
    RABBIT_PAYMENTS_MAIN_EXCHANGE,
    declare_payments_aux_infrastructure,
    payments_rabbit_broker,
)
from app.models.outbox import Outbox

logger = logging.getLogger(__name__)

# Таймаут ожидания publisher ack от брокера (сек.); без успешной публикации строка outbox не помечается processed.
_PUBLISH_CONFIRM_TIMEOUT_SEC = 30.0
_OUTBOX_BATCH_SIZE = 10
_POLL_INTERVAL_SEC = 5


def _message_body_for_outbox(row: Outbox) -> dict[str, Any]:
    """Единый envelope для RabbitMQ: payload + event_type из строки outbox + версия события."""
    body: dict[str, Any] = dict(row.payload)
    body["event_type"] = row.event_type
    body.setdefault("event_version", 1)
    return body


async def publish_outbox_messages() -> None:
    broker = payments_rabbit_broker(consumer_prefetch=None)
    try:
        await broker.connect()
        await declare_payments_aux_infrastructure(broker)
        logger.info("Outbox publisher started, polling every %ss", _POLL_INTERVAL_SEC)

        while True:
            try:
                async with async_session_maker() as db:
                    async with db.begin():
                        result = await db.execute(
                            select(Outbox)
                            .where(Outbox.processed.is_(False))
                            .order_by(Outbox.created_at, Outbox.id)
                            .limit(_OUTBOX_BATCH_SIZE)
                            .with_for_update(skip_locked=True)
                        )
                        rows = result.scalars().all()

                        for row in rows:
                            envelope = _message_body_for_outbox(row)
                            await broker.publish(
                                json.dumps(envelope).encode("utf-8"),
                                exchange=RABBIT_PAYMENTS_MAIN_EXCHANGE,
                                routing_key=PAYMENTS_NEW_QUEUE,
                                persist=True,
                                timeout=_PUBLISH_CONFIRM_TIMEOUT_SEC,
                            )
                            row.processed = True
                            row.processed_at = func.now()
                            logger.info("Published outbox event %s (type=%s)", row.id, row.event_type)

            except Exception:
                logger.exception("Error during outbox poll — will retry after %ss", _POLL_INTERVAL_SEC)

            await asyncio.sleep(_POLL_INTERVAL_SEC)
    finally:
        await broker.stop()
        logger.info("Outbox publisher stopped")


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    asyncio.run(publish_outbox_messages())
