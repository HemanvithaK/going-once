"""Records the outcome bidding decided. The core API owns orders, so it writes them here.

Bidding decides who won and at what price. This service never decides anything. It
only writes what the outcome says, once. A repeated outcome is ignored, because the
listing has already left LIVE.
"""

from enum import StrEnum

from auction_contracts.events import AuctionSettled, AuctionStatus, parse_event
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.listing import Listing, ListingStatus
from app.models.order import Order

log = get_logger(__name__)


class OutcomeResult(StrEnum):
    RECORDED = "recorded"
    ALREADY_SETTLED = "already_settled"
    UNKNOWN_LISTING = "unknown_listing"
    IGNORED = "ignored"  # a valid event this service does not act on
    MALFORMED = "malformed"  # not a usable event; skipped, because retrying cannot fix it


async def record_outcome(session: AsyncSession, raw: bytes | str) -> OutcomeResult:
    """Apply one auction-outcomes message inside the caller's transaction."""
    try:
        event = parse_event(raw)
    except ValidationError:
        return OutcomeResult.MALFORMED
    if not isinstance(event, AuctionSettled):
        return OutcomeResult.IGNORED

    # Lock the listing, so a repeated or concurrent outcome waits here and then sees
    # that the listing has already left LIVE.
    listing = await session.scalar(
        select(Listing).where(Listing.id == event.listing_id).with_for_update()
    )
    if listing is None:
        log.warning("outcome_for_unknown_listing", listing_id=str(event.listing_id))
        return OutcomeResult.UNKNOWN_LISTING
    if listing.status is not ListingStatus.LIVE:
        return OutcomeResult.ALREADY_SETTLED

    if event.status is AuctionStatus.SOLD:
        # The contract guarantees a sold outcome names a winner and a price.
        assert event.winner_id is not None
        assert event.final_price is not None
        listing.status = ListingStatus.SOLD
        listing.winner_id = event.winner_id
        listing.current_price = event.final_price
        session.add(
            Order(
                listing_id=listing.id,
                buyer_id=event.winner_id,
                seller_id=listing.seller_id,
                amount=event.final_price,
            )
        )
        # flush() sends the order now. The UNIQUE(listing_id) constraint is the backstop.
        await session.flush()
    else:
        listing.status = ListingStatus.UNSOLD

    # Nothing is announced from here. Viewers hear about the sale from bidding.
    return OutcomeResult.RECORDED