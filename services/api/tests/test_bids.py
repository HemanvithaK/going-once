import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.models.bid import Bid
from app.models.listing import Listing
from tests.helpers import create_listing, create_live_listing, make_user


async def place(
    client: httpx.AsyncClient,
    listing_id: str,
    headers: dict[str, str],
    amount: Decimal | str,
    key: str,
) -> httpx.Response:
    return await client.post(
        f"/listings/{listing_id}/bids",
        json={"max_amount": str(amount)},
        headers={**headers, "Idempotency-Key": key},
    )


# --- the proxy price rules -----------------------------------------------


async def test_first_bid_leads_at_the_start_price(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    buyer = await make_user(client, "buyer@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")

    response = await place(client, listing["id"], buyer, "150.00", "first-bid-key")
    assert response.status_code == 201, response.text
    assert response.json()["current_price"] == "100.00"
    assert response.json()["is_leading"] is True


async def test_price_climbs_only_as_far_as_needed(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    carol = await make_user(client, "carol@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]

    # Alice opens at a ceiling of 120. She leads, and the price is still 100.
    await place(client, lid, alice, "120.00", "alice-key-1")

    # Bob's ceiling is 150. He beats Alice, but pays only 5 over her ceiling.
    bob_response = await place(client, lid, bob, "150.00", "bob-key-0001")
    assert bob_response.json()["current_price"] == "125.00"
    assert bob_response.json()["is_leading"] is True

    # Carol's ceiling is 130. She is below Bob's, so Bob keeps the lead.
    # The price moves to just above Carol's ceiling, not to Bob's full ceiling.
    carol_response = await place(client, lid, carol, "130.00", "carol-key-1")
    assert carol_response.json()["current_price"] == "135.00"
    assert carol_response.json()["is_leading"] is False


async def test_a_bid_below_the_minimum_is_409(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    await place(client, listing["id"], alice, "120.00", "alice-key-2")

    # The price is 100, so the next bid needs to be at least 105.
    response = await place(client, listing["id"], bob, "102.00", "bob-key-0002")
    assert response.status_code == 409


# --- who may bid, and when ------------------------------------------------


async def test_a_seller_cannot_bid_on_their_own_listing(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller)
    response = await place(client, listing["id"], seller, "500.00", "self-bid-key")
    assert response.status_code == 403


async def test_a_draft_is_invisible_so_it_cannot_be_bid_on(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    buyer = await make_user(client, "buyer@example.com")
    draft = await create_listing(client, seller)
    response = await place(client, draft["id"], buyer, "500.00", "draft-bid-key")
    assert response.status_code == 404


async def test_a_scheduled_listing_is_not_accepting_bids(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    buyer = await make_user(client, "buyer@example.com")
    now = datetime.now(UTC)
    listing = await create_listing(
        client,
        seller,
        starts_at=(now + timedelta(days=1)).isoformat(),
        ends_at=(now + timedelta(days=3)).isoformat(),
    )
    await client.post(f"/listings/{listing['id']}/publish", headers=seller)
    response = await place(client, listing["id"], buyer, "500.00", "sched-bid-key")
    assert response.status_code == 409


async def test_a_bid_after_the_end_time_is_409(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    buyer = await make_user(client, "buyer@example.com")
    listing = await create_live_listing(client, seller)

    # Push the end time into the past directly. The API would refuse to.
    async with SessionLocal() as session:
        await session.execute(
            update(Listing)
            .where(Listing.id == uuid.UUID(listing["id"]))
            .values(ends_at=datetime.now(UTC) - timedelta(seconds=30))
        )
        await session.commit()

    response = await place(client, listing["id"], buyer, "500.00", "late-bid-key")
    assert response.status_code == 409


async def test_a_bid_requires_login(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller)
    response = await client.post(
        f"/listings/{listing['id']}/bids",
        json={"max_amount": "500.00"},
        headers={"Idempotency-Key": "anon-bid-key"},
    )
    assert response.status_code == 401


# --- idempotency ------------------------------------------------------------


async def test_a_retry_with_the_same_key_adds_no_second_bid(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    buyer = await make_user(client, "buyer@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")

    first = await place(client, listing["id"], buyer, "150.00", "retry-key-01")
    second = await place(client, listing["id"], buyer, "150.00", "retry-key-01")

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]

    async with SessionLocal() as session:
        count = len(
            list(
                await session.scalars(select(Bid).where(Bid.listing_id == uuid.UUID(listing["id"])))
            )
        )
    assert count == 1


async def test_reusing_a_key_for_a_different_amount_is_409(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    buyer = await make_user(client, "buyer@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")

    await place(client, listing["id"], buyer, "150.00", "reuse-key-01")
    response = await place(client, listing["id"], buyer, "200.00", "reuse-key-01")
    assert response.status_code == 409


async def test_a_bid_without_an_idempotency_key_is_422(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    buyer = await make_user(client, "buyer@example.com")
    listing = await create_live_listing(client, seller)
    response = await client.post(
        f"/listings/{listing['id']}/bids",
        json={"max_amount": "150.00"},
        headers=buyer,
    )
    assert response.status_code == 422


# --- concurrency ------------------------------------------------------------


async def test_parallel_bids_lose_no_updates(client: httpx.AsyncClient) -> None:
    """100 bidders bid at once. The stored price must match what the accepted bids imply."""
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="1.00")
    listing_id = listing["id"]

    amounts = [Decimal(100 + 3 * i) for i in range(100)]
    random.Random(42).shuffle(amounts)
    bidders = [await make_user(client, f"bidder{i}@example.com") for i in range(len(amounts))]

    responses = await asyncio.gather(
        *(
            place(client, listing_id, headers, amount, f"parallel-{i:04d}")
            for i, (headers, amount) in enumerate(zip(bidders, amounts, strict=True))
        )
    )

    accepted = [
        amount for amount, r in zip(amounts, responses, strict=True) if r.status_code == 201
    ]
    rejected = [r for r in responses if r.status_code == 409]
    assert len(accepted) + len(rejected) == len(amounts), [r.status_code for r in responses]
    assert accepted, "at least one bid should be accepted"

    async with SessionLocal() as session:
        stored = list(
            await session.scalars(select(Bid).where(Bid.listing_id == uuid.UUID(listing_id)))
        )
        stored_listing = await session.get(Listing, uuid.UUID(listing_id))
    assert stored_listing is not None

    # No accepted bid may be lost, and no rejected bid may be stored.
    assert sorted(b.max_amount for b in stored) == sorted(accepted)

    # With all ceilings known, the price must be what the proxy rules give:
    # the top ceiling, capped by the second ceiling plus one increment.
    ceilings = sorted(accepted, reverse=True)
    if len(ceilings) == 1:
        expected = Decimal("100.00")
    else:
        expected = min(ceilings[0], ceilings[1] + Decimal("1.00"))
    assert stored_listing.current_price == expected