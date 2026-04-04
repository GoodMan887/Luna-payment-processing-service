import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.payment import PaymentCreate, PaymentDetail, PaymentResponse
from app.services.payment import PaymentService
from app.core.config import settings


router = APIRouter(prefix="payments")


async def verify_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    if x_api_key is None or x_api_key != settings.x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


@router.post(
    "/",
    response_model=PaymentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_payment(
    data: PaymentCreate,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    service = PaymentService(db)
    return await service.create_payment(data, idempotency_key)


@router.get(
    "/{payment_id}",
    response_model=PaymentDetail,
)
async def get_payment_by_id(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    service = PaymentService(db)
    payment = await service.get_payment(payment_id)
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )
    return payment
