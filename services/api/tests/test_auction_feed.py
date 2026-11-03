import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models.listing import Listing
from app.models.outbox import OutboxEvent
from app.services.auction_feed import FeedResult, apply_auction_update
from tests.helpers import create_live_listing, feed_auction_update, make_user


async def read_listing(listing_id: str) -> Listing:
    async with SessionLocal() as session:
        listing = await session.scalar(select(Listing).where(Listing.id == uuid.UUID(listing_id)))
    assert listing is not None
    return listing


async def test_an_update_sets_the_current_price_and_the_deadline(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00")
    new_end = datetime.now(UTC) + timedelta(days=5)
    raw = json.dumps(
        {
            "type": "auction.updated",
            "schema_version": 1,
            "listing_id": listing["id"],
            "version": 1,
            "status": "live",
            "current_price": "125.00",
            "ends_at": new_end.isoformat(),
        }
    ).encode()

    async with SessionLocal() as session, session.begin():
        assert await apply_auction_update(session, raw) is FeedResult.APPLIED

    stored = await read_listing(listing["id"])
    assert stored.current_price == Decimal("125.00")
    assert stored.ends_at == new_end


async def test_an_update_for_an_unknown_listing_is_reported_not_applied() -> None:
    assert (
        await feed_auction_update(str(uuid.uuid4()), price="125.00")
    ) is FeedResult.UNKNOWN_LISTING


async def test_a_malformed_update_is_skipped() -> None:
    async with SessionLocal() as session, session.begin():
        assert await apply_auction_update(session, b"not json") is FeedResult.MALFORMED


async def test_an_event_this_service_does_not_use_is_ignored() -> None:
    raw = json.dumps(
        {
            "type": "auction.settled",
            "schema_version": 1,
            "listing_id": str(uuid.uuid4()),
            "version": 1,
            "status": "unsold",
            "winner_id": None,
            "final_price": None,
        }
    ).encode()
    async with SessionLocal() as session, session.begin():
        assert await apply_auction_update(session, raw) is FeedResult.IGNORED


async def test_an_update_queues_nothing_for_viewers(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller)

    async def outbox_count() -> int:
        async with SessionLocal() as session:
            return int(await session.scalar(select(func.count()).select_from(OutboxEvent)) or 0)

    before = await outbox_count()  # the publish
    assert await feed_auction_update(listing["id"], price="125.00") is FeedResult.APPLIED

    # Viewers hear about price changes from bidding, so the API announces nothing.
    assert await outbox_count() == before