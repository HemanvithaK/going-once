import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from auction_contracts.events import AUCTION_UPDATES_TOPIC, AuctionUpdated, parse_event
from sqlalchemy import func, select, update

from app.core.db import SessionLocal
from app.models import Auction, Bid, OutboxEvent
from tests.helpers import auth_headers, open_auction


async def bid(
    client: httpx.AsyncClient,
    listing_id: uuid.UUID,
    bidder: uuid.UUID,
    amount: str,
    key: str,
) -> httpx.Response:
    return await client.post(
        f"/listings/{listing_id}/bids",
        json={"max_amount": amount},
        headers={**auth_headers(bidder), "Idempotency-Key": key},
    )


async def read_auction(listing_id: uuid.UUID) -> Auction:
    async with SessionLocal() as session:
        auction = await session.get(Auction, listing_id)
    assert auction is not None
    return auction


async def count_rows(model: type[Bid] | type[OutboxEvent]) -> int:
    async with SessionLocal() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


# --- the proxy price rules ---------------------------------------------------


async def test_first_bid_leads_at_the_start_price(client: httpx.AsyncClient) -> None:
    listing_id, _ = await open_auction(start="100.00", increment="5.00")

    response = await bid(client, listing_id, uuid.uuid4(), "150.00", "first-bid-key")

    assert response.status_code == 201, response.text
    assert response.json()["current_price"] == "100.00"
    assert response.json()["is_leading"] is True
    # A 201 means the bid is already in the database, not just on its way.
    assert await count_rows(Bid) == 1


async def test_an_outbid_price_rises_one_increment_above_the_leader_at_most(
    client: httpx.AsyncClient,
) -> None:
    listing_id, _ = await open_auction()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    assert (await bid(client, listing_id, alice, "150.00", "alice-key-1")).status_code == 201

    response = await bid(client, listing_id, bob, "120.00", "bob-key-01")

    assert response.status_code == 201, response.text
    assert response.json()["current_price"] == "125.00"
    assert response.json()["is_leading"] is False


async def test_a_higher_ceiling_takes_the_lead_one_increment_above_the_old_ceiling(
    client: httpx.AsyncClient,
) -> None:
    listing_id, _ = await open_auction()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    assert (await bid(client, listing_id, alice, "150.00", "alice-key-1")).status_code == 201

    response = await bid(client, listing_id, bob, "160.00", "bob-key-01")

    assert response.status_code == 201, response.text
    assert response.json()["current_price"] == "155.00"
    assert response.json()["is_leading"] is True


async def test_a_bid_below_the_minimum_is_rejected(client: httpx.AsyncClient) -> None:
    listing_id, _ = await open_auction()
    alice, bob, carol = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert (await bid(client, listing_id, alice, "150.00", "alice-key-1")).status_code == 201
    assert (await bid(client, listing_id, bob, "120.00", "bob-key-01")).status_code == 201

    response = await bid(client, listing_id, carol, "124.00", "carol-key-1")

    assert response.status_code == 409
    assert "130.00" in response.json()["detail"]


# --- rules that refuse the bid outright --------------------------------------


async def test_an_unknown_listing_is_not_found(client: httpx.AsyncClient) -> None:
    response = await bid(client, uuid.uuid4(), uuid.uuid4(), "150.00", "missing-key-1")
    assert response.status_code == 404


async def test_a_seller_cannot_bid_on_their_own_listing(client: httpx.AsyncClient) -> None:
    seller = uuid.uuid4()
    listing_id, _ = await open_auction(seller_id=seller)

    response = await bid(client, listing_id, seller, "150.00", "self-bid-key-1")

    assert response.status_code == 403


async def test_a_listing_that_is_not_live_rejects_bids(client: httpx.AsyncClient) -> None:
    listing_id, _ = await open_auction()
    async with SessionLocal() as session, session.begin():
        await session.execute(
            update(Auction).where(Auction.listing_id == listing_id).values(status="unsold")
        )

    response = await bid(client, listing_id, uuid.uuid4(), "150.00", "closed-key-01")

    assert response.status_code == 409


async def test_an_ended_auction_rejects_bids(client: httpx.AsyncClient) -> None:
    listing_id, _ = await open_auction(ends_in=timedelta(seconds=-60))

    response = await bid(client, listing_id, uuid.uuid4(), "150.00", "late-key-0001")

    assert response.status_code == 409
    assert "ended" in response.json()["detail"]


async def test_a_request_without_a_token_is_unauthorized(client: httpx.AsyncClient) -> None:
    listing_id, _ = await open_auction()

    response = await client.post(
        f"/listings/{listing_id}/bids",
        json={"max_amount": "150.00"},
        headers={"Idempotency-Key": "anon-key-0001"},
    )

    assert response.status_code == 401


# --- retries --------------------------------------------------------------------


async def test_a_retry_with_the_same_key_returns_the_same_bid(client: httpx.AsyncClient) -> None:
    listing_id, _ = await open_auction()
    bidder = uuid.uuid4()

    first = await bid(client, listing_id, bidder, "150.00", "retry-key-01")
    second = await bid(client, listing_id, bidder, "150.00", "retry-key-01")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert await count_rows(Bid) == 1
    assert (await read_auction(listing_id)).version == 1


async def test_a_reused_key_for_a_different_amount_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    listing_id, _ = await open_auction()
    bidder = uuid.uuid4()
    assert (await bid(client, listing_id, bidder, "150.00", "reuse-key-01")).status_code == 201

    response = await bid(client, listing_id, bidder, "160.00", "reuse-key-01")

    assert response.status_code == 409
    assert await count_rows(Bid) == 1


# --- what an accepted bid leaves behind ------------------------------------------


async def test_each_accepted_bid_queues_one_auction_updated_event(
    client: httpx.AsyncClient,
) -> None:
    listing_id, _ = await open_auction()

    response = await bid(client, listing_id, uuid.uuid4(), "150.00", "outbox-key-1")
    assert response.status_code == 201

    async with SessionLocal() as session:
        rows = list(await session.scalars(select(OutboxEvent)))
    assert len(rows) == 1
    row = rows[0]
    assert row.topic == AUCTION_UPDATES_TOPIC
    assert row.partition_key == str(listing_id)
    assert row.published_at is None

    event = parse_event(json.dumps(row.payload))
    assert isinstance(event, AuctionUpdated)
    assert event.version == 1
    assert event.status == "live"
    assert event.current_price == Decimal("100.00")


async def test_a_bid_in_the_final_window_pushes_the_deadline_out(
    client: httpx.AsyncClient,
) -> None:
    listing_id, _ = await open_auction(ends_in=timedelta(seconds=10))

    response = await bid(client, listing_id, uuid.uuid4(), "150.00", "snipe-key-01")

    assert response.status_code == 201
    auction = await read_auction(listing_id)
    assert auction.ends_at - datetime.now(UTC) > timedelta(seconds=25)


# --- concurrency ------------------------------------------------------------------


async def test_two_bids_arriving_together_are_applied_one_after_the_other(
    client: httpx.AsyncClient,
) -> None:
    listing_id, _ = await open_auction(start="100.00", increment="5.00")

    first, second = await asyncio.gather(
        bid(client, listing_id, uuid.uuid4(), "150.00", "race-key-0001"),
        bid(client, listing_id, uuid.uuid4(), "150.00", "race-key-0002"),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    auction = await read_auction(listing_id)
    assert auction.version == 2
    assert auction.current_price == Decimal("150.00")
    assert await count_rows(Bid) == 2
