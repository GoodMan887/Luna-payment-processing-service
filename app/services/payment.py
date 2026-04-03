from sqlalchemy.ext.asyncio import AsyncSession
from app.models.payment import Payment, PaymentStatus
from app.schemas.payment import PaymentCreate
from sqlalchemy import select


class PaymentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_payment(self, data: PaymentCreate) -> Payment:
        result = await self.db.execute(
            select(Payment).where(
                Payment.idempotency_key == data.idempotency_key)
        )
        existing_payment = result.scalar_one_or_none()
        if existing_payment:
            return existing_payment

        new_payment = Payment(
            amount=data.amount,
            currency=data.currency,
            description=data.description,
            idempotency_key=data.idempotency_key,
            status=PaymentStatus.PENDING,
            payment_metadata=data.payment_metadata
        )

        self.db.add(new_payment)

        await self.db.commit()
        await self.db.refresh(new_payment)
        return new_payment
