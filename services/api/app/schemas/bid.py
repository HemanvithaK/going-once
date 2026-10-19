import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class BidCreate(BaseModel):
    """A bidder's ceiling: the most they are willing to pay."""

    max_amount: Decimal = Field(gt=0, decimal_places=2, max_digits=12)


class BidOut(BaseModel):
    """The result of a bid, as the bidder sees it."""

    id: uuid.UUID
    listing_id: uuid.UUID
    max_amount: Decimal
    current_price: Decimal
    is_leading: bool
    created_at: datetime