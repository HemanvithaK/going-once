"""Applies bidding's auction.updated events to the listing read model the API serves.

Bidding owns the price and the deadline. The API keeps a copy, so its read endpoints show
current values. Viewers do not hear about a change from here: realtime gets public
updates straight from bidding.
"""

from enum import StrEnum

from auction_contracts.events import AuctionUpdated, parse_event
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.listing import Listing

log = get_logger(__name__)


class FeedResult(StrEnum):
    APPLIED = "applied"
    UNKNOWN_LISTING = "unknown_listing"
    IGNORED = "ignored"  # a valid event this service does not use
    MALFORMED = "malformed"  # not a usable event; skipped, because retrying cannot fix it


async def apply_auction_update(session: AsyncSession, raw: bytes | str) -> FeedResult:
    """Apply one auction-updates message inside the caller's transaction."""
    try:
        event = parse_event(raw)
    except ValidationError:
        return FeedResult.MALFORMED
    if not isinstance(event, AuctionUpdated):
        return FeedResult.IGNORED

    listing = await session.scalar(
        select(Listing).where(Listing.id == event.listing_id).with_for_update()
    )
    if listing is None:
        log.warning("update_for_unknown_listing", listing_id=str(event.listing_id))
        return FeedResult.UNKNOWN_LISTING

    listing.current_price = event.current_price
    listing.ends_at = event.ends_at
    return FeedResult.APPLIED
