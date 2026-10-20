import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.models.listing import Listing, ListingStatus
from app.models.order import Order
from app.services import closing
from app.services.closing import close_all_due
from tests.helpers import create_live_listing, make_user, place_bid


class SimulatedCrashError(Exception):
    """Stands in for kill -9: the process dies before the transaction commits."""


async def set_deadline(listing_id: str, seconds_from_now: float) -> None:
    """Move the deadline using the database clock, so the test and server agree."""
    async with SessionLocal() as session, session.begin():
        await session.execute(
            update(Listing)
            .where(Listing.id == uuid.UUID(listing_id))
            .values(ends_at=func.clock_timestamp() + timedelta(seconds=seconds_from_now))
        )


async def load(listing_id: str) -> Listing:
    async with SessionLocal() as session:
        listing = await session.get(Listing, uuid.UUID(listing_id))
    assert listing is not None
    return listing


async def load_order(listing_id: str) -> Order | None:
    async with SessionLocal() as session:
        return await session.scalar(
            select(Order).where(Order.listing_id == uuid.UUID(listing_id))
        )


async def count_orders(listing_id: str) -> int:
    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count()).select_from(Order).where(Order.listing_id == uuid.UUID(listing_id))
        )
    return int(count or 0)


async def user_id(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    response = await client.get("/auth/me", headers=headers)
    return str(response.json()["id"])


# --- the deadline moves when bids arrive late ----------------------------------


async def test_a_bid_in_the_last_moments_extends_the_deadline(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]

    # The auction would end in 2 seconds. Alice bids inside the anti-snipe window.
    await set_deadline(lid, seconds_from_now=2)
    before = await load(lid)
    response = await place_bid(client, lid, alice, "150.00", "snipe-key-001")
    assert response.status_code == 201, response.text

    after = await load(lid)
    assert after.ends_at > before.ends_at + timedelta(seconds=20)

    # Wait past the original deadline. The auction must still be open.
    await asyncio.sleep(2.5)
    assert await close_all_due() == 0
    assert (await load(lid)).status is ListingStatus.LIVE


# --- closing: winners, reserves, and empty auctions ------------------------------


async def test_a_due_auction_closes_to_the_top_bidder_at_the_second_price(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]
    await place_bid(client, lid, alice, "120.00", "alice-close-1")
    await place_bid(client, lid, bob, "150.00", "bob-close-001")
    await set_deadline(lid, seconds_from_now=-1)  # already past

    assert await close_all_due() == 1
    closed = await load(lid)
    order = await load_order(lid)
    assert closed.status is ListingStatus.SOLD
    assert closed.winner_id is not None
    assert str(closed.winner_id) == await user_id(client, bob)
    assert order is not None
    assert order.amount == Decimal("125.00")


async def test_an_auction_below_its_reserve_ends_unsold_with_no_order(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller, reserve_price="500.00")
    lid = listing["id"]
    await place_bid(client, lid, alice, "300.00", "reserve-key-1")
    await set_deadline(lid, seconds_from_now=-1)

    assert await close_all_due() == 1
    assert (await load(lid)).status is ListingStatus.UNSOLD
    assert await load_order(lid) is None


async def test_an_auction_with_no_bids_ends_unsold_cleanly(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    await set_deadline(lid, seconds_from_now=-1)

    assert await close_all_due() == 1
    closed = await load(lid)
    assert closed.status is ListingStatus.UNSOLD
    assert closed.winner_id is None
    assert await load_order(lid) is None


async def test_a_met_reserve_sells_at_the_reserve_not_the_start_price(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(
        client, seller, start_price="100.00", reserve_price="200.00"
    )
    lid = listing["id"]
    # Alice is the only bidder, so the current price stays at the 100 start price.
    await place_bid(client, lid, alice, "250.00", "reserve-key-2")
    await set_deadline(lid, seconds_from_now=-1)

    assert await close_all_due() == 1
    order = await load_order(lid)
    assert (await load(lid)).status is ListingStatus.SOLD
    assert order is not None
    assert order.amount == Decimal("200.00")


# --- several closers, and a closer that dies ------------------------------------


async def test_four_closers_running_at_once_produce_one_order_per_auction(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    bidder = await make_user(client, "bidder@example.com")
    listing_ids: list[str] = []
    for i in range(10):
        listing = await create_live_listing(client, seller, title=f"Lot {i}")
        await place_bid(client, listing["id"], bidder, "150.00", f"lot-key-{i:03d}")
        await set_deadline(listing["id"], seconds_from_now=-1)
        listing_ids.append(listing["id"])

    results = await asyncio.gather(*(close_all_due() for _ in range(4)))

    assert sum(results) == 10
    for lid in listing_ids:
        assert await count_orders(lid) == 1


async def test_a_closer_killed_mid_close_leaves_nothing_behind_and_a_restart_finishes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    await place_bid(client, lid, alice, "150.00", "crash-key-001")
    await set_deadline(lid, seconds_from_now=-1)

    original_record_sale = closing._record_sale

    async def die_after_writing_the_order(
        session: AsyncSession,
        listing: Listing,
        buyer_id: uuid.UUID,
        amount: Decimal,
    ) -> None:
        await original_record_sale(session, listing, buyer_id, amount)
        raise SimulatedCrashError("killed after the order was written, before commit")

    monkeypatch.setattr(closing, "_record_sale", die_after_writing_the_order)
    with pytest.raises(SimulatedCrashError):
        await close_all_due()

    # The crash rolled the whole transaction back: no order, still LIVE.
    assert await load_order(lid) is None
    assert (await load(lid)).status is ListingStatus.LIVE

    # Restart the closer. It finishes the job exactly once.
    monkeypatch.setattr(closing, "_record_sale", original_record_sale)
    assert await close_all_due() == 1
    assert await count_orders(lid) == 1
    assert (await load(lid)).status is ListingStatus.SOLD


async def test_the_database_refuses_a_second_order_for_the_same_listing(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    await place_bid(client, lid, alice, "150.00", "backstop-key-1")
    await set_deadline(lid, seconds_from_now=-1)
    assert await close_all_due() == 1
    existing = await load_order(lid)
    assert existing is not None

    with pytest.raises(IntegrityError):
        async with SessionLocal() as session, session.begin():
            session.add(
                Order(
                    listing_id=existing.listing_id,
                    buyer_id=existing.buyer_id,
                    seller_id=existing.seller_id,
                    amount=Decimal("1.00"),
                )
            )
    assert await count_orders(lid) == 1


# --- browse does not show auctions whose deadline has passed --------------------


async def test_an_auction_past_its_deadline_is_hidden_from_browse_before_the_closer_runs(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    await set_deadline(lid, seconds_from_now=-1)

    response = await client.get("/listings")
    shown = [item["id"] for item in response.json()["items"]]
    assert lid not in shown