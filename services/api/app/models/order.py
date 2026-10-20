import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Order(Base):
    """The sale of one listing. A listing can have at most one order."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # unique=True is the database backstop: a second order for the same
    # listing cannot be inserted, even if two closers raced past each other.
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="RESTRICT"),
        unique=True,
    )
    buyer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    __table_args__ = (CheckConstraint("amount > 0", name="ck_orders_amount_positive"),)

    def __repr__(self) -> str:
        return f"<Order {self.amount} listing={self.listing_id}>"