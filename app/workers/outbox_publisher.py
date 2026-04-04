import asyncio
import json
from typing import Any

import aio_pika  # pyright: ignore[reportMissingImports]
from sqlalchemy import func, select

from app.core.database import async_session_maker
from app.core.messaging import PAYMENTS_NEW_QUEUE, get_rabbit_channel
from app.models.outbox import Outbox

# Таймаут ожидания publisher ack от брокера (сек.); без успешной публикации строка outbox не помечается processed.
_PUBLISH_CONFIRM_TIMEOUT_SEC = 30.0
_OUTBOX_BATCH_SIZE = 10


def _message_body_for_outbox(row: Outbox) -> dict[str, Any]:
    """Единый envelope для RabbitMQ: payload + event_type из строки outbox + версия события."""
    body: dict[str, Any] = dict(row.payload)
    body["event_type"] = row.event_type
    body.setdefault("event_version", 1)
    return body


async def publish_outbox_messages() -> None:
    connection, channel, main_exchange = await get_rabbit_channel(
        publisher_confirms=True
    )
    async with connection:
        while True:
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
                        await main_exchange.publish(
                            aio_pika.Message(
                                body=json.dumps(envelope).encode("utf-8"),
                                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                            ),
                            routing_key=PAYMENTS_NEW_QUEUE,
                            timeout=_PUBLISH_CONFIRM_TIMEOUT_SEC,
                        )
                        row.processed = True
                        row.processed_at = func.now()

            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(publish_outbox_messages())
