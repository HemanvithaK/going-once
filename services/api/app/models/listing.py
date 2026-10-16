import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ListingStatus(enum.StrEnum):
    """Where a listing is in its life. Transitions are one-way."""

    DRAFT = "draft"  # created, not visible to buyers
    SCHEDULED = "scheduled"  # published, bidding has not opened
    LIVE = "live"  # accepting bids
    SOLD = "sold"  # closed, reserve met
    UNSOLD = "unsold"  # closed, no bids or reserve not met
    CANCELLED = "cancelled"  # pulled by the seller before any bid


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )

    title: Mapped[str] = mapped_column(String(140))
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))

    # Money is NUMERIC, never float. 12 digits total, 2 after the point.
    start_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reserve_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    bid_increment: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default=text("1.00"))

    # Denormalised: the highest bid so far. Kept in step with `bids` inside
    # the same transaction that inserts a bid.
    current_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Mutable: an anti-snipe bid pushes this out.
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    status: Mapped[ListingStatus] = mapped_column(
        Enum(
            ListingStatus,
            name="listing_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=ListingStatus.DRAFT,
        server_default=ListingStatus.DRAFT.value,
    )
    winner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        default=None,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("start_price > 0", name="ck_listings_start_price_positive"),
        CheckConstraint("bid_increment > 0", name="ck_listings_increment_positive"),
        CheckConstraint(
            "reserve_price IS NULL OR reserve_price >= start_price",
            name="ck_listings_reserve_at_least_start",
        ),
        CheckConstraint("ends_at > starts_at", name="ck_listings_ends_after_starts"),
        CheckConstraint(
            "current_price >= start_price",
            name="ck_listings_current_at_least_start",
        ),
        # The browse query: live listings, soonest to close first.
        Index("ix_listings_status_ends_at", "status", "ends_at"),
    )

    def __repr__(self) -> str:
        return f"<Listing {self.title!r} {self.status} {self.current_price}>"