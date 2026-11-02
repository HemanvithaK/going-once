"""Close auctions whose deadline has passed. Bidding decides every outcome.

Each close is one transaction. The auction's status, its version, and the outbox rows
for auction.updated and auction.settled are written together, or not at all.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import SessionLocal
from app.core.events import queue_auction_settled, queue_auction_updated
from app.core.logging import get_logger
from app.models.auction import Auction
from app.services.bidding import current_leader

log = get_logger(__name__)


async def close_next_due(
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> bool:
    """Close the most overdue auction. Returns False when nothing is due."""
    async with session_factory() as session, session.begin():
        auction = await session.scalar(
            select(Auction)
            .where(Auction.status == "live", Auction.ends_at <= func.clock_timestamp())
            .order_by(Auction.ends_at, Auction.listing_id)
            .limit(1)
            # SKIP LOCKED: a row another closer holds is skipped, not waited on.
            .with_for_update(skip_locked=True)
        )
        if auction is None:
            return False
        await _settle(session, auction)
    return True


async def close_all_due(
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> int:
    """Close everything that is due right now. Returns how many were closed."""
    closed = 0
    while await close_next_due(session_factory):
        closed += 1
    return closed


async def _settle(session: AsyncSession, auction: Auction) -> None:
    """Decide one auction's outcome. Runs under the row lock taken by the caller."""
    leader = await current_leader(session, auction.listing_id)
    reserve = auction.reserve_price

    # No bids, or the best ceiling is below the seller's reserve: nobody buys.
    if leader is None or (reserve is not None and leader.max_amount < reserve):
        auction.status = "unsold"
        auction.version += 1
        queue_auction_updated(session, auction)
        queue_auction_settled(session, auction, winner_id=None, final_price=None)
        log.info("auction_unsold", listing_id=str(auction.listing_id), had_bids=leader is not None)
        return

    # The seller never receives less than the reserve. The reserve is never above the
    # winner's ceiling (checked just above), so the winner never pays more than agreed.
    amount = auction.current_price if reserve is None else max(auction.current_price, reserve)
    auction.status = "sold"
    auction.current_price = amount
    auction.version += 1
    queue_auction_updated(session, auction)
    queue_auction_settled(session, auction, winner_id=leader.bidder_id, final_price=amount)
    log.info("auction_sold", listing_id=str(auction.listing_id), amount=str(amount))
