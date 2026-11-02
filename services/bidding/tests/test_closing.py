import asyncio
import json
import uuid
from decimal import Decimal

import pytest
from auction_contracts.events import AUCTION_OUTCOMES_TOPIC, AuctionSettled, parse_event
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Auction, OutboxEvent
from app.services import closing
from app.services.closing import close_all_due
from tests.helpers import make_due, open_auction, place


async def settled_events() -> list[AuctionSettled]:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(OutboxEvent).where(OutboxEvent.topic == AUCTION_OUTCOMES_TOPIC)
        )
        events = [parse_event(json.dumps(row.payload)) for row in rows]
    assert all(isinstance(event, AuctionSettled) for event in events)
    return [event for event in events if isinstance(event, AuctionSettled)]


async def read_status(listing_id: uuid.UUID) -> str:
    async with SessionLocal() as session:
        auction = await session.get(Auction, listing_id)
    assert auction is not None
    return auction.status


async def test_a_due_auction_sells_to_the_top_bidder_at_the_second_price(client) -> None:
    listing_id, _ = await open_auction(start="100.00", increment="5.00")
    alice, bob = uuid.uuid4(), uuid.uuid4()
    assert (await place(client, listing_id, alice, "150.00", "alice-key-01")).status_code == 201
    assert (await place(client, listing_id, bob, "120.00", "bob-key-001")).status_code == 201
    await make_due(listing_id)

    assert await close_all_due() == 1

    assert await read_status(listing_id) == "sold"
    [event] = await settled_events()
    assert event.status == "sold"
    assert event.winner_id == alice
    assert event.final_price == Decimal("125.00")


async def test_a_met_reserve_sells_at_the_reserve_not_the_second_price(client) -> None:
    listing_id, _ = await open_auction(start="100.00", increment="5.00", reserve="140.00")
    alice, bob = uuid.uuid4(), uuid.uuid4()
    assert (await place(client, listing_id, alice, "150.00", "alice-key-01")).status_code == 201
    assert (await place(client, listing_id, bob, "120.00", "bob-key-001")).status_code == 201
    await make_due(listing_id)

    await close_all_due()

    [event] = await settled_events()
    assert event.final_price == Decimal("140.00")


async def test_an_auction_below_its_reserve_is_unsold_with_no_winner(client) -> None:
    listing_id, _ = await open_auction(start="100.00", increment="5.00", reserve="300.00")
    assert (
        await place(client, listing_id, uuid.uuid4(), "150.00", "alice-key-01")
    ).status_code == 201
    await make_due(listing_id)

    await close_all_due()

    assert await read_status(listing_id) == "unsold"
    [event] = await settled_events()
    assert event.status == "unsold"
    assert event.winner_id is None
    assert event.final_price is None


async def test_an_auction_with_no_bids_is_unsold_cleanly() -> None:
    listing_id, _ = await open_auction()
    await make_due(listing_id)

    await close_all_due()

    assert await read_status(listing_id) == "unsold"
    [event] = await settled_events()
    assert event.status == "unsold"


async def test_an_auction_that_is_not_yet_due_is_left_alone(client) -> None:
    listing_id, _ = await open_auction()
    assert (
        await place(client, listing_id, uuid.uuid4(), "150.00", "alice-key-01")
    ).status_code == 201

    assert await close_all_due() == 0
    assert await read_status(listing_id) == "live"


async def test_four_closers_running_at_once_settle_each_auction_exactly_once(client) -> None:
    for _ in range(3):
        listing_id, _ = await open_auction()
        assert (
            await place(client, listing_id, uuid.uuid4(), "150.00", f"bid-{uuid.uuid4().hex[:8]}")
        ).status_code == 201
        await make_due(listing_id)

    counts = await asyncio.gather(*(close_all_due() for _ in range(4)))

    assert sum(counts) == 3
    assert len(await settled_events()) == 3


async def test_a_crash_mid_close_leaves_the_auction_live_and_a_retry_finishes(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing_id, _ = await open_auction()
    assert (
        await place(client, listing_id, uuid.uuid4(), "150.00", "crash-key-001")
    ).status_code == 201
    await make_due(listing_id)
    real_queue = closing.queue_auction_settled

    def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("closer died mid-close")

    monkeypatch.setattr(closing, "queue_auction_settled", crash)
    with pytest.raises(RuntimeError):
        await close_all_due()
    assert await read_status(listing_id) == "live"
    assert await settled_events() == []

    monkeypatch.setattr(closing, "queue_auction_settled", real_queue)
    assert await close_all_due() == 1
    assert await read_status(listing_id) == "sold"
    assert len(await settled_events()) == 1
