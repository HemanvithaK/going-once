import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Bid(Base):
    """One bid placed on a listing. `max_amount` is the bidder's proxy ceiling.

    The price a bidder actually pays is worked out from the highest ceiling
    and the next highest one. It is stored on the listing, not here.
    """

    __tablename__ = "bids"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="RESTRICT"),
        index=True,
    )
    bidder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    max_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    # Chosen by the client. Retrying the same request with the same key must
    # not create a second bid.
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint("max_amount > 0", name="ck_bids_max_amount_positive"),
        UniqueConstraint("bidder_id", "idempotency_key", name="uq_bids_bidder_idempotency"),
        # Used to find the current leader: highest ceiling on this listing.
        Index("ix_bids_listing_max_amount", "listing_id", "max_amount"),
    )

    def __repr__(self) -> str:
        return f"<Bid {self.max_amount} on {self.listing_id}>"