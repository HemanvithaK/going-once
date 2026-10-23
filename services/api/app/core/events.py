"""Listing events: written to the outbox, and queued for a direct Redis send.

Every change to a listing does two things, both inside the transaction that made it:

1. Writes an outbox row. This is the durable copy. It is saved if and only if the
   change is saved, and the relay sends it to Kafka later (Part C).
2. Queues a direct send to Redis, to run after COMMIT. This still feeds live
   viewers today. It is best effort, and Part D removes it once the Kafka
   consumer takes over.

A rolled-back transaction writes no outbox row and sends nothing.
"""

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis import redis_client

if TYPE_CHECKING:
    from app.models.listing import Listing

log = get_logger(__name__)
_PENDING = "pending_events"

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
    """Record one change: bump the version, write the outbox row, queue the direct send."""
    # Imported here, not at the top: app.core.db imports this module, so a
    # top-level import of the model would loop back into a half-loaded module.
    from app.models.outbox import OutboxEvent

    listing.version += 1
    payload = listing_snapshot(listing)
    session.add(
        OutboxEvent(
            topic=LISTING_EVENTS_TOPIC,
            partition_key=str(listing.id),
            payload=payload,
        )
    )
    pending: list[tuple[str, dict[str, Any]]] = session.info.setdefault(_PENDING, [])
    pending.append((listing_channel(listing.id), payload))


async def publish_pending(session: AsyncSession) -> None:
    """Send the queued direct messages. Call only after a successful commit."""
    pending: list[tuple[str, dict[str, Any]]] = session.info.pop(_PENDING, [])
    for channel, payload in pending:
        try:
            await redis_client.publish(channel, json.dumps(payload))
        except Exception as exc:
            # The change is already committed, and the outbox has the durable copy.
            log.warning("event_publish_failed", channel=channel, error=str(exc))