"""convert status and currency to native PostgreSQL ENUM types

Revision ID: a1b2c3d4e5f6
Revises: 2b1aa23ad258
Create Date: 2026-04-07 01:15:00.000000

Fixes two issues from the initial migration:
- status stored values were uppercase (PENDING/SUCCEEDED/FAILED) in migration
  but SQLAlchemy ORM writes lowercase (pending/succeeded/failed) via enum .value;
  existing rows contain lowercase values — USING clause casts them directly.
- Both columns were VARCHAR (native_enum=False); converted to native PG ENUM types
  so the DB enforces valid values at the type level.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "2b1aa23ad258"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Native PG ENUM type definitions
paymentstatus_type = postgresql.ENUM(
    "pending", "succeeded", "failed",
    name="paymentstatus",
    create_type=False,  # we call .create() explicitly to control checkfirst
)
currency_type = postgresql.ENUM(
    "RUB", "USD", "EUR",
    name="currency",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    # Create native ENUM types (skip if already exist)
    paymentstatus_type.create(bind, checkfirst=True)
    currency_type.create(bind, checkfirst=True)

    # Convert status column: VARCHAR → paymentstatus ENUM
    # Existing rows contain lowercase values written by SQLAlchemy ORM.
    op.execute(
        "ALTER TABLE payments "
        "ALTER COLUMN status TYPE paymentstatus "
        "USING status::paymentstatus"
    )

    # Convert currency column: VARCHAR → currency ENUM
    # Existing rows contain uppercase values (RUB/USD/EUR).
    op.execute(
        "ALTER TABLE payments "
        "ALTER COLUMN currency TYPE currency "
        "USING currency::currency"
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Revert status to VARCHAR; keep lowercase values (matches ORM)
    op.execute(
        "ALTER TABLE payments "
        "ALTER COLUMN status TYPE VARCHAR(16) "
        "USING status::text"
    )

    # Revert currency to VARCHAR
    op.execute(
        "ALTER TABLE payments "
        "ALTER COLUMN currency TYPE VARCHAR(3) "
        "USING currency::text"
    )

    # Drop ENUM types only if no other tables reference them
    paymentstatus_type.drop(bind, checkfirst=True)
    currency_type.drop(bind, checkfirst=True)
