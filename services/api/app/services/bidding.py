import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.events import queue_event
from app.core.logging import get_logger
from app.models.bid import Bid
from app.models.listing import Listing, ListingStatus
from app.models.user import User

log = get_logger(__name__)
_settings = get_settings()


class BidError(Exception):
    """A bid the rules do not allow. The API layer turns it into an HTTP error."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class BidResult:
    bid: Bid
    listing: Listing
    is_leading: bool


async def db_now(session: AsyncSession) -> datetime:
    """The database's clock, read at the moment of the call.

    now() would return the time the transaction *started*, which can be well
    before we got the row lock. clock_timestamp() returns the real current time.
    """
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None  # clock_timestamp() always returns a value
    return now


async def current_leader(session: AsyncSession, listing_id: uuid.UUID) -> Bid | None:
    """The bid with the highest ceiling. A tie goes to whoever bid first."""
    return await session.scalar(
        select(Bid)
        .where(Bid.listing_id == listing_id)
        .order_by(Bid.max_amount.desc(), Bid.created_at.asc())
        .limit(1)
    )


async def place_bid(
    session: AsyncSession,
    bidder: User,
    listing_id: uuid.UUID,
    max_amount: Decimal,
    idempotency_key: str,
) -> BidResult:
    # 1. Lock the listing row. Every bid on this listing now waits here, so
    #    the steps below run one bid at a time for this listing.
    listing = await session.scalar(
        select(Listing).where(Listing.id == listing_id).with_for_update()
    )
    if listing is None:
        raise BidError(404, "listing not found")
    if listing.status is ListingStatus.DRAFT and listing.seller_id != bidder.id:
        raise BidError(404, "listing not found")

    # 2. A retry of a bid we already accepted returns that bid. It must not
    #    add a second bid or move the price again.
    previous = await session.scalar(
        select(Bid).where(Bid.bidder_id == bidder.id, Bid.idempotency_key == idempotency_key)
    )
    if previous is not None:
        if previous.listing_id != listing.id or previous.max_amount != max_amount:
            raise BidError(409, "idempotency key was already used for a different bid")
        leader = await current_leader(session, listing.id)
        return BidResult(
            bid=previous,
            listing=listing,
            is_leading=leader is not None and leader.bidder_id == bidder.id,
        )

    # 3. Rules for a new bid. "now" is read after the lock, so it is the time
    #    this bid is actually applied, not the time the request arrived.
    now = await db_now(session)
    if listing.seller_id == bidder.id:
        raise BidError(403, "sellers cannot bid on their own listing")
    if listing.status is not ListingStatus.LIVE:
        raise BidError(409, "listing is not accepting bids")
    if now >= listing.ends_at:
        raise BidError(409, "auction has ended")

    leader = await current_leader(session, listing.id)
    if leader is None:
        minimum = listing.start_price
    elif leader.bidder_id == bidder.id:
        minimum = leader.max_amount + listing.bid_increment
    else:
        minimum = listing.current_price + listing.bid_increment
    if max_amount < minimum:
        raise BidError(409, f"bid must be at least {minimum}")

    # 4. Record the bid, stamped with the database clock.
    bid = Bid(
        listing_id=listing.id,
        bidder_id=bidder.id,
        max_amount=max_amount,
        idempotency_key=idempotency_key,
        created_at=now,
    )
    session.add(bid)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Same key from the same bidder on a different listing, raced past the check above.
        raise BidError(409, "idempotency key was already used") from exc

    # Anti-snipe. A bid in the final window pushes the deadline out to a full
    # window from now, so nobody can win by bidding in the last second unanswered.
    window = timedelta(seconds=_settings.anti_snipe_seconds)
    if listing.ends_at - now < window:
        listing.ends_at = now + window
        log.info(
            "deadline_extended",
            listing_id=str(listing.id),
            ends_at=listing.ends_at.isoformat(),
        )

    # 5. Proxy pricing. The leader pays just enough to beat the next-highest
    #    ceiling, and never more than their own ceiling.
    increment = listing.bid_increment
    if leader is None:
        new_leader_id = bidder.id
        new_price = listing.start_price
    elif leader.bidder_id == bidder.id:
        new_leader_id = bidder.id
        new_price = listing.current_price  # nobody else to beat
    elif max_amount > leader.max_amount:
        new_leader_id = bidder.id
        new_price = min(max_amount, leader.max_amount + increment)
    else:
        new_leader_id = leader.bidder_id
        new_price = min(leader.max_amount, max_amount + increment)

    listing.current_price = new_price
    log.info(
        "bid_placed",
        listing_id=str(listing.id),
        bidder_id=str(bidder.id),
        current_price=str(new_price),
    )
    queue_event(session, listing)
    return BidResult(bid=bid, listing=listing, is_leading=new_leader_id == bidder.id)