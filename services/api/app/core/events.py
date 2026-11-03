"""Listing events: the outbox is the only way out of a change.

The core API announces one thing: a listing was published. The outbox row is written
inside the transaction that published it, and the relay sends it to Kafka, so a
rolled-back publish announces nothing.

listing-events is private. Bidding reads it to learn about a newly published auction.
It carries the reserve price, so nothing else may subscribe to it. Viewers never hear
from the core API at all: realtime gets public updates straight from bidding.
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.models.listing import Listing

LISTING_EVENTS_TOPIC = "listing-events"


def listing_published(listing: "Listing") -> dict[str, Any]:
    """The contract's listing.published message. It carries the reserve, so it is private."""
    return {
        "type": "listing.published",
        "schema_version": 1,
        "listing_id": str(listing.id),
        "version": listing.version,
        "seller_id": str(listing.seller_id),
        "start_price": str(listing.start_price),
        "bid_increment": str(listing.bid_increment),
        "reserve_price": None if listing.reserve_price is None else str(listing.reserve_price),
        "ends_at": listing.ends_at.isoformat(),
    }


def queue_listing_published(session: AsyncSession, listing: "Listing") -> None:
    """Tell bidding that this listing is now an auction.

    The publish is version 0 of the listing, and it is the only version the core API
    announces. Everything after it is bidding's to announce.
    """
    # Imported here, not at the top: app.core.db imports the models package, so a
    # top-level import of the model would loop back into a half-loaded module.
    from app.models.outbox import OutboxEvent

    session.add(
        OutboxEvent(
            topic=LISTING_EVENTS_TOPIC,
            partition_key=str(listing.id),
            payload=listing_published(listing),
        )
    )