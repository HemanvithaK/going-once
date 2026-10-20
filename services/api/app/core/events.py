"""Events that tell clients what changed, sent to Redis only after a commit.

Rules:
- While a transaction runs, an event is queued on the session.
- After COMMIT succeeds, the queue is sent. A rolled-back transaction sends nothing.
- Sending is best effort. If Redis is down after a commit, the change is still
  saved, and clients catch up on their next snapshot. Phase 7's outbox closes
  this gap.
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


def listing_channel(listing_id: object) -> str:
    return f"listing:{listing_id}"


def listing_snapshot(listing: "Listing") -> dict[str, Any]:
    """The public state of a listing. The reserve is never included."""
    return {
        "type": "listing.updated",
        "listing_id": str(listing.id),
        "status": listing.status.value,
        "current_price": str(listing.current_price),
        "ends_at": listing.ends_at.isoformat(),
    }


def queue_event(session: AsyncSession, listing: "Listing") -> None:
    """Remember a change to send once this transaction commits."""
    pending: list[tuple[str, dict[str, Any]]] = session.info.setdefault(_PENDING, [])
    pending.append((listing_channel(listing.id), listing_snapshot(listing)))


async def publish_pending(session: AsyncSession) -> None:
    """Send the queued events. Call only after a successful commit."""
    pending: list[tuple[str, dict[str, Any]]] = session.info.pop(_PENDING, [])
    for channel, payload in pending:
        try:
            await redis_client.publish(channel, json.dumps(payload))
        except Exception as exc:
            # The change is already committed. Log it and move on.
            log.warning("event_publish_failed", channel=channel, error=str(exc))