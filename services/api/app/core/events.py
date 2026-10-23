"""Listing events: the outbox is the only way out of a change.

Every change to a listing writes one outbox row, inside the transaction that made
the change. The relay sends that row to Kafka, and the consumer forwards it to
Redis, where the realtime hub fans it out to viewers.

A rolled-back transaction writes no outbox row, so nothing is announced for it.
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.models.listing import Listing

LISTING_EVENTS_TOPIC = "listing-events"


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


def queue_event(session: AsyncSession, listing: "Listing") -> None:
    """Record one change: bump the version and write its outbox row."""
    # Imported here, not at the top: app.core.db imports this module, so a
    # top-level import of the model would loop back into a half-loaded module.
    from app.models.outbox import OutboxEvent

    listing.version += 1
    session.add(
        OutboxEvent(
            topic=LISTING_EVENTS_TOPIC,
            partition_key=str(listing.id),
            payload=listing_snapshot(listing),
        )
    )