"""Close auctions whose deadline has passed.

Each close is one database transaction. Either the listing becomes SOLD or
UNSOLD together with its order (if any), or nothing changes and the auction
stays LIVE for the next closer to try. A crash in the middle is a rollback.
"""

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.models.listing import Listing, ListingStatus
from app.models.order import Order
from app.services.bidding import current_leader

log = get_logger(__name__)


async def close_next_due(
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> bool:
    """Close the most overdue auction. Returns False when nothing is due."""
    async with session_factory() as session, session.begin():
        listing = await session.scalar(
            select(Listing)
            .where(
                Listing.status == ListingStatus.LIVE,
                Listing.ends_at <= func.clock_timestamp(),
            )
            .order_by(Listing.ends_at, Listing.id)
            .limit(1)
            # SKIP LOCKED: a row another closer already holds is skipped, not
            # waited on. Many closers can run side by side without queueing.
            .with_for_update(skip_locked=True)
        )
        if listing is None:
            return False
        await _settle(session, listing)
    return True


async def close_all_due(
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> int:
    """Close everything that is due right now. Returns how many were closed."""
    closed = 0
    while await close_next_due(session_factory):
        closed += 1
    return closed


async def _settle(session: AsyncSession, listing: Listing) -> None:
    """Decide one auction's outcome. Runs under the row lock taken by the caller."""
    leader = await current_leader(session, listing.id)
    reserve = listing.reserve_price

    # No bids, or the best ceiling is below the seller's reserve: nobody buys.
    if leader is None or (reserve is not None and leader.max_amount < reserve):
        listing.status = ListingStatus.UNSOLD
        await session.flush()
        log.info("auction_unsold", listing_id=str(listing.id), had_bids=leader is not None)
        return

    # The seller never receives less than the reserve. The reserve is never
    # above the winner's ceiling (checked just above), so the winner never
    # pays more than they agreed to.
    amount = listing.current_price if reserve is None else max(listing.current_price, reserve)
    await _record_sale(session, listing, leader.bidder_id, amount)
    log.info("auction_sold", listing_id=str(listing.id), amount=str(amount))


async def _record_sale(
    session: AsyncSession,
    listing: Listing,
    buyer_id: uuid.UUID,
    amount: Decimal,
) -> None:
    listing.status = ListingStatus.SOLD
    listing.winner_id = buyer_id
    listing.current_price = amount
    session.add(
        Order(
            listing_id=listing.id,
            buyer_id=buyer_id,
            seller_id=listing.seller_id,
            amount=amount,
        )
    )
    # flush() sends the INSERTs and UPDATE now, inside our transaction. The
    # UNIQUE(listing_id) constraint on orders is enforced here as a backstop.
    await session.flush()