import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Auction(Base):
    """This service's copy of a listing's auction, built from listing-events.

    The core API owns listings. This table holds only what bidding needs to decide
    whether a bid is allowed and what it costs.
    """

    __tablename__ = "auctions"

    listing_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(Uuid)

    # live, sold, or unsold. Checked below against the values the contract allows.
    status: Mapped[str] = mapped_column(String(16))
    start_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    bid_increment: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reserve_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    current_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Copied from the listing event. Lets us ignore events that arrive out of order.
    version: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('live', 'sold', 'unsold')",
            name="ck_auctions_status_known",
        ),
        CheckConstraint("start_price > 0", name="ck_auctions_start_price_positive"),
        CheckConstraint("bid_increment > 0", name="ck_auctions_increment_positive"),
        CheckConstraint(
            "reserve_price IS NULL OR reserve_price >= start_price",
            name="ck_auctions_reserve_at_least_start",
        ),
        CheckConstraint(
            "current_price >= start_price",
            name="ck_auctions_current_at_least_start",
        ),
        # Used by the closer: live auctions whose end time has passed.
        Index("ix_auctions_status_ends_at", "status", "ends_at"),
    )

    def __repr__(self) -> str:
        return f"<Auction {self.listing_id} {self.status} {self.current_price}>"
