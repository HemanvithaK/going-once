"""Event contracts between the going-once services.

These models are the only shape that crosses a service boundary. A service
never reads another service's tables. It reads these events, and nothing else.

Every event carries:
- type: which event it is. Consumers switch on this.
- schema_version: bumped only for a breaking change.
- listing_id and version: which listing, and which change to it. Consumers use
  the version to ignore anything older than what they already applied.

Models forbid unknown fields. A producer that adds a field fails loudly in the
tests, instead of having the field silently dropped in production.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

# One topic per audience. The topic is the access control: a service that must not
# see private data simply does not subscribe to the private topic.
LISTING_TOPIC = "listing-events"  # core api -> bidding. Private: contains reserve_price.
AUCTION_UPDATES_TOPIC = "auction-updates"  # bidding -> realtime. Public to every viewer.
AUCTION_OUTCOMES_TOPIC = "auction-outcomes"  # bidding -> core api. Private: names the winner.


class AuctionStatus(StrEnum):
    LIVE = "live"
    SOLD = "sold"
    UNSOLD = "unsold"


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    listing_id: uuid.UUID
    version: int = Field(ge=1)


class ListingPublished(_Event):
    """A seller published a listing. Bidding starts tracking the auction from this."""

    type: Literal["listing.published"] = "listing.published"
    seller_id: uuid.UUID
    start_price: Decimal
    bid_increment: Decimal
    reserve_price: Decimal | None
    ends_at: datetime


class AuctionUpdated(_Event):
    """The public state of an auction. Any connected viewer may receive this.

    There is no reserve_price and no winner here. Adding either would send private
    data to every browser watching the listing, so extra fields are rejected.
    """

    type: Literal["auction.updated"] = "auction.updated"
    status: AuctionStatus
    current_price: Decimal
    ends_at: datetime


class AuctionSettled(_Event):
    """The final outcome of an auction. Private: it names the winner."""

    type: Literal["auction.settled"] = "auction.settled"
    status: Literal[AuctionStatus.SOLD, AuctionStatus.UNSOLD]
    winner_id: uuid.UUID | None = None
    final_price: Decimal | None = None

    @model_validator(mode="after")
    def _winner_matches_status(self) -> "AuctionSettled":
        if self.status == AuctionStatus.SOLD:
            if self.winner_id is None or self.final_price is None:
                raise ValueError("a sold auction needs a winner_id and a final_price")
        elif self.winner_id is not None or self.final_price is not None:
            raise ValueError("an unsold auction has no winner_id or final_price")
        return self


Event = Annotated[
    ListingPublished | AuctionUpdated | AuctionSettled,
    Field(discriminator="type"),
]
_event_adapter = TypeAdapter(Event)


def parse_event(raw: str | bytes) -> ListingPublished | AuctionUpdated | AuctionSettled:
    """Turn one message into a checked event. Raises if the message does not match."""
    return _event_adapter.validate_json(raw)