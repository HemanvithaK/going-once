"""How bidding announces a change. The outbox row is written in the same transaction."""

import uuid
from decimal import Decimal
from typing import Any

from auction_contracts.events import AUCTION_OUTCOMES_TOPIC, AUCTION_UPDATES_TOPIC
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auction import Auction
from app.models.outbox import OutboxEvent


def auction_updated(auction: Auction) -> dict[str, Any]:
    """The public state of an auction, in the shape AuctionUpdated expects."""
    return {
        "type": "auction.updated",
        "schema_version": 1,
        "listing_id": str(auction.listing_id),
        "version": auction.version,
        "status": auction.status,
        "current_price": str(auction.current_price),
        "ends_at": auction.ends_at.isoformat(),
    }


def queue_auction_updated(session: AsyncSession, auction: Auction) -> None:
    """Add the event to the outbox. It goes to Kafka only after this transaction commits."""
    session.add(
        OutboxEvent(
            topic=AUCTION_UPDATES_TOPIC,
            # Same key for every update to one auction, so Kafka keeps them in order.
            partition_key=str(auction.listing_id),
            payload=auction_updated(auction),
        )
    )


def auction_settled(
    auction: Auction,
    *,
    winner_id: uuid.UUID | None,
    final_price: Decimal | None,
) -> dict[str, Any]:
    """The final outcome, in the shape AuctionSettled expects. Private: it names the winner."""
    return {
        "type": "auction.settled",
        "schema_version": 1,
        "listing_id": str(auction.listing_id),
        "version": auction.version,
        "status": auction.status,
        "winner_id": None if winner_id is None else str(winner_id),
        "final_price": None if final_price is None else str(final_price),
    }


def queue_auction_settled(
    session: AsyncSession,
    auction: Auction,
    *,
    winner_id: uuid.UUID | None,
    final_price: Decimal | None,
) -> None:
    session.add(
        OutboxEvent(
            topic=AUCTION_OUTCOMES_TOPIC,
            partition_key=str(auction.listing_id),
            payload=auction_settled(auction, winner_id=winner_id, final_price=final_price),
        )
    )
