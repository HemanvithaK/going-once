import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.events import LISTING_SNAPSHOTS_TOPIC
from app.models.listing import Listing, ListingStatus
from app.models.outbox import OutboxEvent
from app.services import settlement
from app.services.auction_feed import FeedResult
from app.services.settlement import OutcomeResult
from tests.helpers import create_live_listing, feed_auction_update, make_user, settle, user_id


async def outbox_rows(listing_id: str) -> list[OutboxEvent]:
    """This listing's snapshot rows, in version order."""
    async with SessionLocal() as session:
        result = await session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.partition_key == listing_id,
                OutboxEvent.topic == LISTING_SNAPSHOTS_TOPIC,
            )
            .order_by(OutboxEvent.payload["version"].as_integer())
        )
        return list(result)


async def stored_version(listing_id: str) -> int:
    async with SessionLocal() as session:
        version = await session.scalar(
            select(Listing.version).where(Listing.id == uuid.UUID(listing_id))
        )
    assert version is not None
    return int(version)


async def test_a_feed_update_writes_one_outbox_row_with_the_new_state(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]

    assert await feed_auction_update(lid, price="120.00") is FeedResult.APPLIED

    rows = await outbox_rows(lid)
    assert len(rows) == 1
    row = rows[0]
    assert row.topic == LISTING_SNAPSHOTS_TOPIC
    assert row.partition_key == lid
    assert row.published_at is None  # not sent yet: that is the relay's job
    assert row.payload["version"] == 1
    assert row.payload["status"] == "live"
    assert row.payload["current_price"] == "120.00"


async def test_an_update_for_an_unknown_listing_writes_no_outbox_row() -> None:
    assert (
        await feed_auction_update(str(uuid.uuid4()), price="120.00")
    ) is FeedResult.UNKNOWN_LISTING

    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.topic == LISTING_SNAPSHOTS_TOPIC)
        )
    assert count == 0


async def test_each_change_bumps_the_version_and_the_rows_follow_in_order(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    await make_user(client, "buyer@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]

    await feed_auction_update(lid, price="120.00")
    await feed_auction_update(lid, price="150.00")
    outcome = await settle(
        lid,
        status="sold",
        winner=await user_id("buyer@example.com"),
        price="150.00",
    )
    assert outcome is OutcomeResult.RECORDED

    rows = await outbox_rows(lid)
    assert [row.payload["version"] for row in rows] == [1, 2, 3]
    assert [row.payload["status"] for row in rows] == ["live", "live", "sold"]
    assert await stored_version(lid) == 3


async def test_a_failed_outcome_leaves_no_outbox_row_and_a_retry_writes_it_once(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seller = await make_user(client, "seller@example.com")
    await make_user(client, "buyer@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    buyer = await user_id("buyer@example.com")
    await feed_auction_update(lid, price="150.00")
    assert len(await outbox_rows(lid)) == 1  # the feed update only

    real_queue_event = settlement.queue_event

    def die_after_queueing(session, listing_row) -> None:  # type: ignore[no-untyped-def]
        real_queue_event(session, listing_row)
        raise RuntimeError("killed after queueing, before commit")

    monkeypatch.setattr(settlement, "queue_event", die_after_queueing)
    with pytest.raises(RuntimeError):
        await settle(lid, status="sold", winner=buyer, price="150.00")

    # The rollback removed the sale, its order, and its outbox row together.
    assert len(await outbox_rows(lid)) == 1
    async with SessionLocal() as session:
        status = await session.scalar(select(Listing.status).where(Listing.id == uuid.UUID(lid)))
    assert status is ListingStatus.LIVE

    monkeypatch.setattr(settlement, "queue_event", real_queue_event)
    assert await settle(lid, status="sold", winner=buyer, price="150.00") is OutcomeResult.RECORDED
    rows = await outbox_rows(lid)
    assert [row.payload["status"] for row in rows] == ["live", "sold"]
