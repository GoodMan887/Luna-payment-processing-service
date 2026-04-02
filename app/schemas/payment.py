import uuid
from pydantic import BaseModel, Field, HttpUrl
from decimal import Decimal
from typing import Any, Optional
from app.models.payment import Currency, PaymentStatus
from pydantic import ConfigDict


class PaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0, decimal_places=2)
    currency: Currency
    description: Optional[str] = None
    payment_metadata: Optional[dict[str, Any]] = None
    idempotency_key: str
    webhook_url: Optional[HttpUrl] = None


class PaymentResponse(BaseModel):
    id: uuid.UUID
    status: PaymentStatus

    model_config = ConfigDict(from_attributes=True)
