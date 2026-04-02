import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.payment import PaymentStatus
from app.schemas.payment import PaymentCreate, PaymentResponse
from app.services.payment import PaymentService


router = APIRouter()


@router.post("/", response_model=PaymentResponse)
async def create_payment(
    data: PaymentCreate,
    db: AsyncSession = Depends(get_db),
):
    service = PaymentService(db)
    return service.create_payment(data)
