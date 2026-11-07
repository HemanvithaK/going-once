"""Promises bidding makes when things go wrong or happen at the same time.

Each test names one promise and breaks the system the way production would.
"""

import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.db import SessionLocal
from app.main import app
from app.models import Auction, Bid, OutboxEvent
from app.services import bidding
from app.services.auction_state import ApplyOutcome
from app.services.closing import close_all_due
from app.workers import auction_sync, closer
from tests.helpers import make_due, open_auction, place, publish_bytes


async def count(model: type[Bid] | type[OutboxEvent]) -> int:
    async with SessionLocal() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


async def read_auction(listing_id: uuid.UUID) -> Auction:
    async with SessionLocal() as session:
        auction = await session.get(Auction, listing_id)
    assert auction is not None
    return auction


class CommitSpy:
    """Wraps the app. The moment a response starts, it counts bids from a separate connection.

    A separate connection sees only committed rows, so the count is what a client that
    reads right after the 201 would see.
    """

    def __init__(self, inner: ASGIApp) -> None:
        self._inner = inner
        self.bids_visible_at_response: int | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def spying_send(message: Message) -> None:
            if message["type"] == "http.response.start" and scope["path"].endswith("/bids"):
                self.bids_visible_at_response = await count(Bid)
            await send(message)

        await self._inner(scope, receive, spying_send)


async def test_a_201_means_the_bid_is_already_committed() -> None:
    listing_id, _ = await open_auction()
    spy = CommitSpy(app)
    transport = httpx.ASGITransport(app=spy)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await place(client, listing_id, uuid.uuid4(), "150.00", "commit-key-01")

    assert response.status_code == 201
    # When the response started, the bid was already visible to everyone else.
    assert spy.bids_visible_at_response == 1


async def test_a_bid_that_fails_midway_leaves_no_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing_id, _ = await open_auction(start="100.00")

    def die(*args: object, **kwargs: object) -> None:
        raise RuntimeError("process died after the bid was written")

    # By this point the bid row is written and the price is changed in the session.
    monkeypatch.setattr(bidding, "queue_auction_updated", die)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await place(client, listing_id, uuid.uuid4(), "150.00", "crash-key-001")

    assert response.status_code == 500
    assert await count(Bid) == 0
    assert await count(OutboxEvent) == 0
    auction = await read_auction(listing_id)
    assert (auction.current_price, auction.version) == (Decimal("100.00"), 0)


async def test_the_same_request_sent_twice_at_once_makes_one_bid(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing_id, _ = await open_auction()
    bidder = uuid.uuid4()
    real_current_leader = bidding.current_leader

    async def slow_current_leader(session: AsyncSession, listing: uuid.UUID) -> Bid | None:
        # Widens the gap between "is there a bid with this key?" and "write the bid", so
        # two requests would both get through it if nothing made them take turns.
        await asyncio.sleep(0.2)
        return await real_current_leader(session, listing)

    monkeypatch.setattr(bidding, "current_leader", slow_current_leader)

    first, second = await asyncio.gather(
        place(client, listing_id, bidder, "150.00", "double-key-001"),
        place(client, listing_id, bidder, "150.00", "double-key-001"),
    )

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert await count(Bid) == 1
    assert await count(OutboxEvent) == 1  # one announcement, not two
    assert (await read_auction(listing_id)).version == 1


async def test_a_late_bid_keeps_the_closer_away_until_the_new_deadline(
    client: httpx.AsyncClient,
) -> None:
    listing_id, _ = await open_auction(ends_in=timedelta(seconds=1))
    assert (
        await place(client, listing_id, uuid.uuid4(), "150.00", "late-key-001")
    ).status_code == 201

    await asyncio.sleep(1.5)  # past the original deadline

    assert await close_all_due() == 0
    assert (await read_auction(listing_id)).status == "live"


async def test_a_closer_does_not_wait_behind_a_locked_auction() -> None:
    listing_id, _ = await open_auction()
    await make_due(listing_id)

    # Another transaction holds the auction's row lock, as a bid in progress would.
    async with SessionLocal() as holder, holder.begin():
        await holder.scalar(
            select(Auction).where(Auction.listing_id == listing_id).with_for_update()
        )
        # The closer skips the locked row at once instead of queueing behind it.
        assert await asyncio.wait_for(close_all_due(), timeout=2) == 0

    assert await close_all_due() == 1  # once the lock is gone, it closes


async def test_the_closer_loop_survives_a_failed_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def flaky_close_all_due() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("database went away")
        return 0

    monkeypatch.setattr(closer, "close_all_due", flaky_close_all_due)
    task = asyncio.create_task(closer.run_forever(0.01))
    try:
        for _ in range(200):  # up to about two seconds
            if calls >= 3:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert calls >= 3  # it kept going after the first cycle failed


async def test_the_listing_feed_retries_through_a_database_outage_without_skipping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing_id = uuid.uuid4()
    raw = publish_bytes(
        listing_id,
        uuid.uuid4(),
        start="100.00",
        increment="5.00",
        ends_in=timedelta(hours=1),
        reserve=None,
    )
    real_process = auction_sync.process_message
    real_sleep = asyncio.sleep
    attempts = 0

    async def flaky_process(session_factory: object, message: bytes | str) -> ApplyOutcome:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise ConnectionError("database down")
        return await real_process(SessionLocal, message)

    async def no_wait(_: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(auction_sync, "process_message", flaky_process)
    monkeypatch.setattr("app.workers.auction_sync.asyncio.sleep", no_wait)
    await auction_sync._apply_until_database_accepts(SessionLocal, raw)

    assert attempts == 3  # two failures, then it went through
    assert (await read_auction(listing_id)).status == "live"
