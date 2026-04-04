import asyncio
import json

import aio_pika  # pyright: ignore[reportMissingImports]
from sqlalchemy import func, select

from app.core.database import async_session_maker
from app.core.messaging import PAYMENTS_NEW_QUEUE, get_rabbit_channel
from app.models.outbox import Outbox


async def publish_outbox_messages() -> None:
    connection, channel, main_exchange = await get_rabbit_channel()
    async with connection:
        while True:
            async with async_session_maker() as db:
                result = await db.execute(
                    select(Outbox).where(Outbox.processed.is_(False)).limit(10)
                )
                messages = result.scalars().all()

                for msg in messages:
                    await main_exchange.publish(
                        aio_pika.Message(
                            body=json.dumps(msg.payload).encode("utf-8"),
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        ),
                        routing_key=PAYMENTS_NEW_QUEUE,
                    )
                    msg.processed = True
                    msg.processed_at = func.now()

                await db.commit()

            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(publish_outbox_messages())
