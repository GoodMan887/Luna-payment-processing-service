import aio_pika  # pyright: ignore[reportMissingImports]

from app.core.config import settings

# --- Имена сущностей RabbitMQ ---
PAYMENTS_EXCHANGE = "payments.events"
PAYMENTS_DLX_EXCHANGE = "payments.dlx"
PAYMENTS_NEW_QUEUE = "payments.new"
PAYMENTS_RETRY_QUEUE = "payments.new.retry"
PAYMENTS_DLQ_QUEUE = "payments.new.dlq"
PAYMENTS_DLQ_ROUTING_KEY = "payments.new.dlq"

# Ретраи на уровне потребления (не путать с ретраями webhook)
CONSUMER_MAX_ATTEMPTS = 3
# Экспоненциальная пауза перед следующей попыткой: 1s, 2s (мс для per-message TTL в retry-очереди)
CONSUMER_RETRY_BASE_DELAY_MS = 1000

# Заголовок с номером попытки (1..CONSUMER_MAX_ATTEMPTS)
HEADER_ATTEMPT = "x-attempt"
HEADER_FAILURE_REASON = "x-failure-reason"
HEADER_DEAD_LETTER_FROM = "x-dead-letter-from"


async def declare_payments_topology(
    channel: aio_pika.abc.AbstractChannel,
) -> tuple[aio_pika.Exchange, aio_pika.Exchange, aio_pika.Queue]:
    """
    Объявляет обменники и очереди:
    - payments.events: основной direct, в очередь payments.new
    - payments.new: основная очередь; x-dead-letter → payments.dlx / payments.new.dlq
      (сообщения с nack(requeue=False), истёкший TTL и т.п. попадут в DLQ брокером)
    - payments.new.retry: без биндинга на вход; x-dead-letter обратно на payments.events
      с rk payments.new — после истечения per-message expiration сообщение снова в основной очереди
    - payments.new.dlq: очередь «мёртвых» сообщений, привязана к payments.dlx
    """
    main_ex = await channel.declare_exchange(
        PAYMENTS_EXCHANGE,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )
    dlx_ex = await channel.declare_exchange(
        PAYMENTS_DLX_EXCHANGE,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )

    dlq_queue = await channel.declare_queue(PAYMENTS_DLQ_QUEUE, durable=True)
    await dlq_queue.bind(dlx_ex, routing_key=PAYMENTS_DLQ_ROUTING_KEY)

    await channel.declare_queue(
        PAYMENTS_RETRY_QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": PAYMENTS_EXCHANGE,
            "x-dead-letter-routing-key": PAYMENTS_NEW_QUEUE,
        },
    )

    main_queue = await channel.declare_queue(
        PAYMENTS_NEW_QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": PAYMENTS_DLX_EXCHANGE,
            "x-dead-letter-routing-key": PAYMENTS_DLQ_ROUTING_KEY,
        },
    )
    await main_queue.bind(main_ex, routing_key=PAYMENTS_NEW_QUEUE)

    return main_ex, dlx_ex, main_queue


async def publish_to_retry_queue(
    channel: aio_pika.abc.AbstractChannel,
    body: bytes,
    *,
    next_attempt: int,
    delay_ms: int,
) -> None:
    """Публикация в retry-очередь с TTL; по истечении TTL брокер перекладывает сообщение в payments.new."""
    msg = aio_pika.Message(
        body=body,
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        headers={HEADER_ATTEMPT: next_attempt},
        expiration=str(delay_ms),
    )
    await channel.default_exchange.publish(msg, routing_key=PAYMENTS_RETRY_QUEUE)


async def publish_to_dlq(
    dlx_exchange: aio_pika.Exchange,
    body: bytes,
    *,
    failure_reason: str,
    final_attempt: int | None = None,
) -> None:
    """
    Явная отправка в DLQ с причиной в заголовке x-failure-reason
    (invalid_json, missing_payment_id, invalid_payment_id, max_retries_exceeded, …).
    """
    headers: dict[str, str | int] = {
        HEADER_FAILURE_REASON: failure_reason,
        HEADER_DEAD_LETTER_FROM: PAYMENTS_NEW_QUEUE,
    }
    if final_attempt is not None:
        headers["x-final-attempt"] = final_attempt
    await dlx_exchange.publish(
        aio_pika.Message(
            body=body,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers,
        ),
        routing_key=PAYMENTS_DLQ_ROUTING_KEY,
    )


async def get_rabbit_channel() -> tuple[
    aio_pika.RobustConnection,
    aio_pika.abc.AbstractChannel,
    aio_pika.Exchange,
]:
    connection = await aio_pika.connect_robust(str(settings.rabbitmq_url))
    channel = await connection.channel()
    main_ex, _, _ = await declare_payments_topology(channel)
    return connection, channel, main_ex
