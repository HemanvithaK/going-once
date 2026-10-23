import uuid
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.events import LISTING_EVENTS_TOPIC
from app.models.listing import Listing, ListingStatus
from app.models.outbox import OutboxEvent
from app.services import closing
from app.services.closing import close_all_due
from tests.helpers import create_live_listing, make_user, place_bid


async def outbox_rows(listing_id: str) -> list[OutboxEvent]:
    """This listing's outbox rows, in version order."""
    async with SessionLocal() as session:
        result = await session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.partition_key == listing_id)
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


async def set_deadline_past(listing_id: str) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(
            update(Listing)
            .where(Listing.id == uuid.UUID(listing_id))
            .values(ends_at=func.clock_timestamp() - timedelta(seconds=1))
        )


async def test_an_accepted_bid_writes_one_outbox_row_with_the_new_state(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]

    response = await place_bid(client, lid, alice, "120.00", "outbox-alice")
    assert response.status_code == 201, response.text

    rows = await outbox_rows(lid)
    assert len(rows) == 1
    row = rows[0]
    assert row.topic == LISTING_EVENTS_TOPIC
    assert row.partition_key == lid
    assert row.published_at is None  # not sent yet: that is the relay's job (Part C)
    assert row.payload["version"] == 1
    assert row.payload["status"] == "live"
    assert row.payload["current_price"] == "100.00"


async def test_a_rejected_bid_writes_no_outbox_row(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]
    await place_bid(client, lid, alice, "120.00", "outbox-reject-alice")

    # The minimum is 105, so 102 is refused and the transaction rolls back.
    response = await place_bid(client, lid, bob, "102.00", "outbox-reject-bob")
    assert response.status_code == 409

    rows = await outbox_rows(lid)
    assert [row.payload["version"] for row in rows] == [1]  # only Alice's accepted bid


async def test_each_change_bumps_the_version_and_the_rows_follow_in_order(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]

    await place_bid(client, lid, alice, "120.00", "versions-alice")
    await place_bid(client, lid, bob, "150.00", "versions-bob")
    await set_deadline_past(lid)
    assert await close_all_due() == 1

    rows = await outbox_rows(lid)
    assert [row.payload["version"] for row in rows] == [1, 2, 3]
    assert [row.payload["status"] for row in rows] == ["live", "live", "sold"]
    assert await stored_version(lid) == 3


async def test_a_closer_that_crashes_after_queueing_leaves_no_outbox_row(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    await place_bid(client, lid, alice, "150.00", "crash-outbox-alice")
    await set_deadline_past(lid)
    assert len(await outbox_rows(lid)) == 1  # Alice's bid only

    original_record_sale = closing._record_sale

    async def die_after_queueing(
        session: AsyncSession,
        listing: Listing,
        buyer_id: uuid.UUID,
        amount: Decimal,
    ) -> None:
        await original_record_sale(session, listing, buyer_id, amount)
        raise RuntimeError("killed after the sale was queued, before commit")

    monkeypatch.setattr(closing, "_record_sale", die_after_queueing)
    with pytest.raises(RuntimeError):
        await close_all_due()

    # The rollback removed the sale's outbox row along with the sale itself.
    assert len(await outbox_rows(lid)) == 1
    async with SessionLocal() as session:
        status = await session.scalar(select(Listing.status).where(Listing.id == uuid.UUID(lid)))
    assert status is ListingStatus.LIVE

    # A restarted closer writes the sale exactly once.
    monkeypatch.setattr(closing, "_record_sale", original_record_sale)
    assert await close_all_due() == 1
    rows = await outbox_rows(lid)
    assert [row.payload["status"] for row in rows] == ["live", "sold"]