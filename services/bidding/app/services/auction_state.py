"""Applies listing-events to this service's auctions table.

An event is applied only when its version is newer than the version already stored.
A replay, or an event that arrives late, is therefore harmless.
"""

from enum import StrEnum

from auction_contracts.events import AuctionStatus, ListingPublished, parse_event
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auction import Auction


class ApplyOutcome(StrEnum):
    APPLIED = "applied"  # stored as a new auction, or as a newer version of one
    STALE = "stale"  # the stored version is already this one or newer
    IGNORED = "ignored"  # a valid event this service does not act on
    MALFORMED = "malformed"  # not a usable event; skipped, because retrying cannot fix it


def _breaks_auction_rules(event: ListingPublished) -> bool:
    """The database would refuse these rows. Check first, so the message is skipped, not retried."""
    if event.start_price <= 0 or event.bid_increment <= 0:
        return True
    return event.reserve_price is not None and event.reserve_price < event.start_price


async def apply_listing_event(session: AsyncSession, raw: bytes | str) -> ApplyOutcome:
    try:
        event = parse_event(raw)
    except ValidationError:
        return ApplyOutcome.MALFORMED

    if not isinstance(event, ListingPublished):
        return ApplyOutcome.IGNORED
    if _breaks_auction_rules(event):
        return ApplyOutcome.MALFORMED

    statement = insert(Auction).values(
        listing_id=event.listing_id,
        seller_id=event.seller_id,
        status=AuctionStatus.LIVE.value,
        start_price=event.start_price,
        bid_increment=event.bid_increment,
        reserve_price=event.reserve_price,
        current_price=event.start_price,
        ends_at=event.ends_at,
        version=event.version,
    )
    upsert = statement.on_conflict_do_update(
        index_elements=[Auction.listing_id],
        set_={
            "seller_id": statement.excluded.seller_id,
            "start_price": statement.excluded.start_price,
            "bid_increment": statement.excluded.bid_increment,
            "reserve_price": statement.excluded.reserve_price,
            "ends_at": statement.excluded.ends_at,
            "version": statement.excluded.version,
        },
        where=Auction.version < statement.excluded.version,
    ).returning(Auction.listing_id)

    # RETURNING gives a row only when the insert or update happened. None means stale.
    applied = await session.scalar(upsert)
    return ApplyOutcome.APPLIED if applied is not None else ApplyOutcome.STALE
