import uuid
from datetime import datetime
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
    webhook_url: Optional[HttpUrl] = None


class PaymentResponse(BaseModel):
    payment_id: uuid.UUID = Field(validation_alias="id")
    status: PaymentStatus
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentDetail(BaseModel):
    payment_id: uuid.UUID = Field(validation_alias="id")
    amount: Decimal
    currency: Currency
    description: Optional[str] = None
    payment_metadata: Optional[dict[str, Any]] = None
    status: PaymentStatus
    idempotency_key: str
    webhook_url: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
