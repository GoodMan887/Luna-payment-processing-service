from faststream.rabbit import RabbitBroker  # pyright: ignore[reportMissingImports]
from faststream.rabbit.schemas import (  # pyright: ignore[reportMissingImports]
    Channel,
    ExchangeType,
    RabbitExchange,
    RabbitQueue,
)

from app.core.config import settings

# --- Имена сущностей RabbitMQ ---
PAYMENTS_EXCHANGE = "payments.events"
PAYMENTS_DLX_EXCHANGE = "payments.dlx"
PAYMENTS_NEW_QUEUE = "payments.new"
PAYMENTS_RETRY_QUEUE = "payments.new.retry"
PAYMENTS_DLQ_QUEUE = "payments.new.dlq"
PAYMENTS_DLQ_ROUTING_KEY = "payments.new.dlq"

# Схемы FastStream (обменники / очереди)
RABBIT_PAYMENTS_MAIN_EXCHANGE = RabbitExchange(
    PAYMENTS_EXCHANGE,
    type=ExchangeType.DIRECT,
    durable=True,
)
RABBIT_PAYMENTS_DLX_EXCHANGE = RabbitExchange(
    PAYMENTS_DLX_EXCHANGE,
    type=ExchangeType.DIRECT,
    durable=True,
)
RABBIT_PAYMENTS_MAIN_QUEUE = RabbitQueue(
    PAYMENTS_NEW_QUEUE,
    durable=True,
    arguments={
        "x-dead-letter-exchange": PAYMENTS_DLX_EXCHANGE,
        "x-dead-letter-routing-key": PAYMENTS_DLQ_ROUTING_KEY,
    },
    routing_key=PAYMENTS_NEW_QUEUE,
)
RABBIT_PAYMENTS_DLQ_QUEUE = RabbitQueue(PAYMENTS_DLQ_QUEUE, durable=True)
RABBIT_PAYMENTS_RETRY_QUEUE = RabbitQueue(
    PAYMENTS_RETRY_QUEUE,
    durable=True,
    arguments={
        "x-dead-letter-exchange": PAYMENTS_EXCHANGE,
        "x-dead-letter-routing-key": PAYMENTS_NEW_QUEUE,
    },
)

# Ретраи на уровне потребления (не путать с ретраями webhook)
CONSUMER_MAX_ATTEMPTS = 3
# Экспоненциальная пауза перед следующей попыткой: 1s, 2s (мс для per-message TTL в retry-очереди)
CONSUMER_RETRY_BASE_DELAY_MS = 1000

# Заголовок с номером попытки (1..CONSUMER_MAX_ATTEMPTS)
HEADER_ATTEMPT = "x-attempt"
HEADER_FAILURE_REASON = "x-failure-reason"
HEADER_DEAD_LETTER_FROM = "x-dead-letter-from"


def payments_rabbit_broker(*, consumer_prefetch: int | None = 1) -> RabbitBroker:
    """Брокер FastStream; prefetch задаётся только для воркера-потребителя."""
    ch = (
        Channel(prefetch_count=consumer_prefetch)
        if consumer_prefetch is not None
        else Channel()
    )
    return RabbitBroker(str(settings.rabbitmq_url), default_channel=ch)


async def declare_payments_aux_infrastructure(broker: RabbitBroker) -> None:
    """
    Объявляет обменники и вспомогательные очереди до старта subscriber'а основной очереди:
    - payments.events, payments.dlx
    - payments.new.dlq + bind к payments.dlx
    - payments.new.retry (перекладывание в payments.new по TTL через DLX)
    Очередь payments.new объявляет и биндит FastStream при подписке.
    """
    dlx = await broker.declare_exchange(RABBIT_PAYMENTS_DLX_EXCHANGE)
    await broker.declare_exchange(RABBIT_PAYMENTS_MAIN_EXCHANGE)
    dlq = await broker.declare_queue(RABBIT_PAYMENTS_DLQ_QUEUE)
    await dlq.bind(dlx, routing_key=PAYMENTS_DLQ_ROUTING_KEY)
    await broker.declare_queue(RABBIT_PAYMENTS_RETRY_QUEUE)


async def publish_to_retry_queue(
    broker: RabbitBroker,
    body: bytes,
    *,
    next_attempt: int,
    delay_ms: int,
) -> None:
    """Публикация в retry-очередь с TTL; по истечении TTL брокер перекладывает сообщение в payments.new."""
    await broker.publish(
        body,
        routing_key=PAYMENTS_RETRY_QUEUE,
        persist=True,
        expiration=str(delay_ms),
        headers={HEADER_ATTEMPT: next_attempt},
    )


async def publish_to_dlq(
    broker: RabbitBroker,
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
    await broker.publish(
        body,
        exchange=RABBIT_PAYMENTS_DLX_EXCHANGE,
        routing_key=PAYMENTS_DLQ_ROUTING_KEY,
        persist=True,
        headers=headers,
    )
