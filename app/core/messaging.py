import aio_pika  # pyright: ignore[reportMissingImports]
from app.core.config import settings

PAYMENTS_NEW_QUEUE = "payments.new"


async def get_rabbit_channel():
    connection = await aio_pika.connect_robust(str(settings.rabbitmq_url))
    channel = await connection.channel()
    await channel.declare_queue(PAYMENTS_NEW_QUEUE, durable=True)
    return connection, channel
