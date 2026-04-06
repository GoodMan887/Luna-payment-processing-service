# Async Payment Processing Service

Микросервис асинхронной обработки платежей на FastAPI + RabbitMQ + PostgreSQL.

## Стек технологий

| Компонент | Технология |
|-----------|-----------|
| API-фреймворк | FastAPI + Pydantic v2 |
| Брокер сообщений | RabbitMQ (через FastStream) |
| База данных | PostgreSQL 16 |
| ORM | SQLAlchemy 2.0 (async) |
| Миграции | Alembic |
| HTTP-клиент | httpx (webhook-уведомления) |
| Контейнеризация | Docker + Docker Compose |

## Архитектура

```
              ┌─────────────────────────────────────────────────────┐
              │                  Docker Compose                     │
              │                                                     │
  Client ────▶│  api :8000  ──▶  outbox table  ──▶  outbox        │
              │                                       publisher     │
              │                                          │          │
              │                                          ▼          │
              │                                      RabbitMQ       │
              │                                    payments.new     │
              │                                          │          │
              │                                          ▼          │
              │                                      consumer       │
              │                                    (обработка +     │
              │                                     webhook)        │
              └─────────────────────────────────────────────────────┘
```

Сервис разбит на три процесса:

- **api** — принимает HTTP-запросы, сохраняет платёж и outbox-запись в одной транзакции
- **outbox_publisher** — опрашивает таблицу `outbox` и публикует события в RabbitMQ
- **consumer** — получает сообщения из очереди, эмулирует обработку, обновляет статус, отправляет webhook

## Реализованные паттерны

### Outbox Pattern

При создании платежа API атомарно записывает:
1. Строку в таблицу `payments`
2. Событие в таблицу `outbox` (в той же транзакции)

Отдельный процесс `outbox_publisher` читает необработанные строки с блокировкой `SELECT ... FOR UPDATE SKIP LOCKED` и публикует их в RabbitMQ только после получения publisher confirm от брокера. Это гарантирует доставку событий даже при сбоях после коммита в БД.

### Idempotency Key

Каждый запрос на создание платежа требует заголовок `Idempotency-Key` (UUID). При повторном запросе с тем же ключом возвращается уже существующий платёж без создания дубликата. Ключ хранится в БД с уникальным индексом.

### Dead Letter Queue (DLQ)

Сообщения, которые не удалось обработать за 3 попытки, попадают в очередь `payments.new.dlq` через обменник `payments.dlx`. Промежуточные попытки идут через `payments.new.retry` с экспоненциальной задержкой (1 с, 2 с) на основе per-message TTL + DLX.

### Retry логика

- **Consumer (RabbitMQ):** до 3 попыток с задержкой 1 с → 2 с; после — DLQ
- **Webhook-уведомления:** до 3 HTTP-попыток с задержкой 1 с → 2 с; клиентские ошибки 4xx (кроме 429) не ретраятся

## Запуск

### Требования

- Docker >= 24
- Docker Compose >= 2.20

### 1. Подготовка переменных окружения

```bash
cp .env.example .env.docker
```

Отредактируйте `.env.docker` при необходимости (по умолчанию всё готово для локального запуска):

```env
ENVIRONMENT=development
DEBUG=false
DATABASE_URL=postgresql+asyncpg://payments:payments@postgres:5432/payments
RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
X_API_KEY=changeme
```

### 2. Запуск

```bash
docker compose up --build
```

При старте автоматически выполняется сервис `migrate`, который применяет все миграции Alembic (`alembic upgrade head`) до запуска API и воркеров. Порядок гарантируется через `depends_on` с healthcheck-ами PostgreSQL и RabbitMQ.

После успешного запуска:

| Сервис | Адрес |
|--------|-------|
| API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| RabbitMQ Management | http://localhost:15672 (guest / guest) |

### Остановка

```bash
docker compose down
```

Для полной очистки томов (БД и очереди):

```bash
docker compose down -v
```

## API

Все эндпоинты требуют заголовок `X-API-Key`.

### Создание платежа

```bash
curl -X POST http://localhost:8000/api/v1/payments/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: changeme" \
  -H "Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000" \
  -d '{
    "amount": "1500.00",
    "currency": "RUB",
    "description": "Оплата заказа №42",
    "payment_metadata": {"order_id": 42},
    "webhook_url": "https://example.com/webhook"
  }'
```

Ответ `202 Accepted`:

```json
{
  "payment_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "pending",
  "created_at": "2026-04-07T10:00:00.000Z"
}
```

Повторный запрос с тем же `Idempotency-Key` вернёт тот же ответ без создания нового платежа.

### Получение статуса платежа

```bash
curl http://localhost:8000/api/v1/payments/3fa85f64-5717-4562-b3fc-2c963f66afa6 \
  -H "X-API-Key: changeme"
```

Ответ `200 OK`:

```json
{
  "payment_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "amount": "1500.00",
  "currency": "RUB",
  "description": "Оплата заказа №42",
  "payment_metadata": {"order_id": 42},
  "status": "succeeded",
  "idempotency_key": "550e8400-e29b-41d4-a716-446655440000",
  "webhook_url": "https://example.com/webhook",
  "created_at": "2026-04-07T10:00:00.000Z",
  "processed_at": "2026-04-07T10:00:04.000Z"
}
```

Возможные значения `status`: `pending`, `succeeded`, `failed`.

### Коды ответов

| Код | Описание |
|-----|----------|
| `202` | Платёж принят в обработку |
| `200` | Детали платежа |
| `401` | Неверный или отсутствующий `X-API-Key` |
| `404` | Платёж не найден |
| `422` | Ошибка валидации (невалидный UUID, неизвестная валюта и т.д.) |

## Структура проекта

```
.
├── app/
│   ├── api/v1/routers/    # FastAPI-роутеры
│   ├── core/              # Конфиг, БД, настройки RabbitMQ
│   ├── models/            # SQLAlchemy-модели (Payment, Outbox)
│   ├── schemas/           # Pydantic-схемы запросов/ответов
│   ├── services/          # Бизнес-логика (PaymentService)
│   └── workers/
│       ├── payment_consumer.py   # FastStream consumer
│       └── outbox_publisher.py   # Outbox polling publisher
├── alembic/               # Миграции БД
├── Dockerfile
├── docker-compose.yml
└── .env.example
```
