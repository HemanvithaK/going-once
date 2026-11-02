"""Listing events: the outbox is the only way out of a change.

Every change to a listing writes outbox rows, inside the transaction that made the
change. The relay sends each row to its topic, and a rolled-back transaction writes
nothing, so nothing is announced for it.

There are two topics, and the topic decides who reads what:

- listing-events: private. Bidding reads it to learn about a newly published
  auction. It carries the reserve price, so nothing else may subscribe to it.
- listing-snapshots: the viewer feed. Phase 7's realtime path reads it. Part E
  replaces it with auction-updates, and this topic goes away.
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.models.listing import Listing

LISTING_EVENTS_TOPIC = "listing-events"
LISTING_SNAPSHOTS_TOPIC = "listing-snapshots"


def listing_channel(listing_id: object) -> str:
    return f"listing:{listing_id}"


def listing_snapshot(listing: "Listing") -> dict[str, Any]:
    """The public state of a listing. The reserve is never included."""
    return {
        "type": "listing.updated",
        "listing_id": str(listing.id),
        "version": listing.version,
        "status": listing.status.value,
        "current_price": str(listing.current_price),
        "ends_at": listing.ends_at.isoformat(),
    }


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


def queue_event(session: AsyncSession, listing: "Listing") -> None:
    """Record one change: bump the version and write its snapshot to the viewer feed."""
    # Imported here, not at the top: app.core.db imports this module, so a
    # top-level import of the model would loop back into a half-loaded module.
    from app.models.outbox import OutboxEvent

    listing.version += 1
    session.add(
        OutboxEvent(
            topic=LISTING_SNAPSHOTS_TOPIC,
            partition_key=str(listing.id),
            payload=listing_snapshot(listing),
        )
    )


def queue_listing_published(session: AsyncSession, listing: "Listing") -> None:
    """Tell bidding that this listing is now an auction. Does not bump the version.

    The publish is version 0 of the listing. Bids that follow start at version 1.
    """
    from app.models.outbox import OutboxEvent

    session.add(
        OutboxEvent(
            topic=LISTING_EVENTS_TOPIC,
            partition_key=str(listing.id),
            payload=listing_published(listing),
        )
    )
