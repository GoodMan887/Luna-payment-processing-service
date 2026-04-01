import uuid
from datetime import datetime
from typing import Any

from app.core.database import Base
from sqlalchemy import DateTime, JSON, String, Uuid, func, Boolean
from sqlalchemy.orm import Mapped, mapped_column


class Outbox(Base):
    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    processed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
