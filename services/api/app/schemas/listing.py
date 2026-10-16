import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.listing import Listing, ListingStatus
from app.models.user import User


class ListingCreate(BaseModel):
    """What a seller sends to create a listing."""

    title: str = Field(min_length=3, max_length=140)
    description: str = Field(default="", max_length=10_000)
    start_price: Decimal = Field(gt=0, decimal_places=2, max_digits=12)
    reserve_price: Decimal | None = Field(default=None, gt=0, decimal_places=2, max_digits=12)
    bid_increment: Decimal = Field(default=Decimal("1.00"), gt=0, decimal_places=2, max_digits=12)
    starts_at: datetime
    ends_at: datetime

    @field_validator("starts_at", "ends_at")
    @classmethod
    def timezone_is_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("must include a timezone offset, such as +00:00")
        return value

    @model_validator(mode="after")
    def check_coherent(self) -> "ListingCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.reserve_price is not None and self.reserve_price < self.start_price:
            raise ValueError("reserve_price cannot be below start_price")
        return self


class ListingUpdate(BaseModel):
    """Every field optional: only what is sent gets changed."""

    title: str | None = Field(default=None, min_length=3, max_length=140)
    description: str | None = Field(default=None, max_length=10_000)
    start_price: Decimal | None = Field(default=None, gt=0, decimal_places=2, max_digits=12)
    reserve_price: Decimal | None = Field(default=None, gt=0, decimal_places=2, max_digits=12)
    bid_increment: Decimal | None = Field(default=None, gt=0, decimal_places=2, max_digits=12)
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @field_validator("title", "description", "start_price", "bid_increment", "starts_at", "ends_at")
    @classmethod
    def cannot_be_cleared(cls, value: object) -> object:
        # Only reserve_price may be sent as null: "no reserve" is a real state.
        # Every other column is NOT NULL in the database.
        if value is None:
            raise ValueError("this field cannot be cleared")
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def timezone_is_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("must include a timezone offset, such as +00:00")
        return value


class ListingOut(BaseModel):
    """A listing as the outside world sees it."""

    id: uuid.UUID
    seller_id: uuid.UUID
    title: str
    description: str
    start_price: Decimal
    bid_increment: Decimal
    current_price: Decimal
    starts_at: datetime
    ends_at: datetime
    status: ListingStatus
    created_at: datetime

    # Everyone may know whether a reserve exists; only the seller sees what it is.
    has_reserve: bool
    reserve_price: Decimal | None = None


class ListingPage(BaseModel):
    """One page of listings, plus how to ask for the next one."""

    items: list[ListingOut]
    next_cursor: str | None = None


def listing_out(listing: Listing, viewer: User | None) -> ListingOut:
    """Build the public view of a listing, hiding the reserve from buyers."""
    is_seller = viewer is not None and viewer.id == listing.seller_id
    return ListingOut(
        id=listing.id,
        seller_id=listing.seller_id,
        title=listing.title,
        description=listing.description,
        start_price=listing.start_price,
        bid_increment=listing.bid_increment,
        current_price=listing.current_price,
        starts_at=listing.starts_at,
        ends_at=listing.ends_at,
        status=listing.status,
        created_at=listing.created_at,
        has_reserve=listing.reserve_price is not None,
        reserve_price=listing.reserve_price if is_seller else None,
    )