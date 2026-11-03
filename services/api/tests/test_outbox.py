"""The outbox: what the core API writes, and what it never writes."""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.api import listings as listings_module
from app.core.db import SessionLocal
from app.core.events import LISTING_EVENTS_TOPIC, queue_listing_published
from app.models.listing import Listing, ListingStatus
from app.models.outbox import OutboxEvent
from app.services.auction_feed import FeedResult
from app.services.settlement import OutcomeResult
from tests.helpers import (
    create_listing,
    create_live_listing,
    feed_auction_update,
    make_user,
    settle,
    user_id,
)


async def outbox_rows() -> list[OutboxEvent]:
    """Every outbox row, oldest first."""
    async with SessionLocal() as session:
        return list(await session.scalars(select(OutboxEvent).order_by(OutboxEvent.id)))


async def listing_count() -> int:
    async with SessionLocal() as session:
        return int(await session.scalar(select(func.count()).select_from(Listing)) or 0)


async def test_publishing_writes_one_unsent_listing_event(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(
        client, seller, start_price="100.00", bid_increment="5.00", reserve_price="150.00"
    )

    [row] = await outbox_rows()
    assert row.topic == LISTING_EVENTS_TOPIC
    assert row.partition_key == listing["id"]
    assert row.published_at is None  # not sent yet: that is the relay's job
    assert row.payload["type"] == "listing.published"
    assert row.payload["version"] == 0
    assert row.payload["start_price"] == "100.00"
    assert row.payload["reserve_price"] == "150.00"  # private: bidding needs it


async def test_a_draft_announces_nothing(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    await create_listing(client, seller)

    assert await outbox_rows() == []


async def test_a_feed_update_and_a_settlement_queue_nothing_for_viewers(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    await make_user(client, "buyer@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    before = len(await outbox_rows())  # the publish

    assert await feed_auction_update(lid, price="120.00") is FeedResult.APPLIED
    outcome = await settle(
        lid, status="sold", winner=await user_id("buyer@example.com"), price="120.00"
    )
    assert outcome is OutcomeResult.RECORDED

    # Viewers hear about both from bidding, not from here.
    assert len(await outbox_rows()) == before


async def test_a_failed_publish_rolls_back_the_listing_and_its_event(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_listing(client, seller)
    lid = listing["id"]

    def die_after_queueing(session: Any, listing_row: Any) -> None:
        queue_listing_published(session, listing_row)
        raise RuntimeError("killed after queueing, before commit")

    monkeypatch.setattr(listings_module, "queue_listing_published", die_after_queueing)
    with pytest.raises(RuntimeError):
        await client.post(f"/listings/{lid}/publish", headers=seller)

    # The rollback removed the status change and the outbox row together.
    assert await outbox_rows() == []
    async with SessionLocal() as session:
        status = await session.scalar(select(Listing.status).where(Listing.id == uuid.UUID(lid)))
    assert status is ListingStatus.DRAFT

    monkeypatch.undo()
    retry = await client.post(f"/listings/{lid}/publish", headers=seller)
    assert retry.status_code == 200
    [row] = await outbox_rows()
    assert row.partition_key == lid