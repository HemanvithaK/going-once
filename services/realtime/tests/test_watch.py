import asyncio
import json
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any

import httpx
import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from app.core.channels import auction_channel
from app.core.hub import hub
from app.workers.forwarder import Forwarder
from tests.fakes import FakeBidding, auction_state, free_port, update_bytes

SERVICE_DIR = Path(__file__).resolve().parents[1]
RECEIVE_TIMEOUT = 3.0
QUIET_PERIOD = 0.5


async def receive(ws: ClientConnection) -> dict[str, Any]:
    message: dict[str, Any] = json.loads(await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT))
    return message


async def assert_silent(ws: ClientConnection) -> None:
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(ws.recv(), timeout=QUIET_PERIOD)


async def test_a_watcher_gets_the_current_state_on_connect(
    bidding: FakeBidding, realtime_url: str
) -> None:
    listing_id = uuid.uuid4()
    bidding.put(auction_state(listing_id, version=3, price="115.00"))

    async with connect(f"{realtime_url}/ws/listings/{listing_id}") as ws:
        snapshot = await receive(ws)

    assert snapshot["type"] == "auction.updated"
    assert snapshot["listing_id"] == str(listing_id)
    assert snapshot["status"] == "live"
    assert snapshot["current_price"] == "115.00"
    assert snapshot["version"] == 3


async def test_an_update_is_pushed_to_a_connected_watcher(
    bidding: FakeBidding, realtime_url: str
) -> None:
    listing_id = uuid.uuid4()
    bidding.put(auction_state(listing_id, version=0, price="100.00"))

    async with connect(f"{realtime_url}/ws/listings/{listing_id}") as ws:
        assert (await receive(ws))["current_price"] == "100.00"

        await Forwarder().handle(update_bytes(listing_id, version=1, price="125.00"))
        update = await receive(ws)

    assert update["current_price"] == "125.00"
    assert update["status"] == "live"
    assert update["version"] == 1


async def test_updates_not_newer_than_the_snapshot_are_skipped(
    bidding: FakeBidding, realtime_url: str
) -> None:
    listing_id = uuid.uuid4()
    bidding.put(auction_state(listing_id, version=5, price="150.00"))

    async with connect(f"{realtime_url}/ws/listings/{listing_id}") as ws:
        assert (await receive(ws))["version"] == 5

        forwarder = Forwarder()
        for version, price in [(4, "145.00"), (5, "150.00"), (6, "155.00")]:
            await forwarder.handle(update_bytes(listing_id, version=version, price=price))

        # Only version 6 is news. Showing 4 would move the price backwards.
        assert (await receive(ws))["version"] == 6
        await assert_silent(ws)


async def test_an_update_that_arrives_while_the_snapshot_is_in_flight_cannot_move_the_price_back(
    bidding: FakeBidding, realtime_url: str
) -> None:
    """The race the version check exists for: the viewer has joined, bidding is still
    answering with version 5, and a version 4 message comes through Redis meanwhile."""
    listing_id = uuid.uuid4()
    channel = auction_channel(listing_id)
    bidding.put(auction_state(listing_id, version=5, price="150.00"))
    bidding.delay_seconds = 0.6

    async with connect(f"{realtime_url}/ws/listings/{listing_id}") as ws:
        for _ in range(100):  # wait until the server has joined, but not yet answered
            if hub.watcher_count(channel) == 1:
                break
            await asyncio.sleep(0.01)
        assert hub.watcher_count(channel) == 1
        await Forwarder().handle(update_bytes(listing_id, version=4, price="145.00"))

        first = await receive(ws)
        assert first["version"] == 5  # the snapshot, not the older update
        await assert_silent(ws)


async def test_an_unknown_auction_is_closed_with_a_not_found_code(
    bidding: FakeBidding, realtime_url: str
) -> None:
    async with connect(f"{realtime_url}/ws/listings/{uuid.uuid4()}") as ws:
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT)

    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == 4404


async def test_a_viewer_is_told_to_try_again_when_bidding_is_down(
    bidding: FakeBidding, realtime_url: str
) -> None:
    bidding.fail_status = 503

    async with connect(f"{realtime_url}/ws/listings/{uuid.uuid4()}") as ws:
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT)

    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == 1013


async def test_a_failed_snapshot_does_not_leave_the_viewer_subscribed(
    bidding: FakeBidding, realtime_url: str
) -> None:
    listing_id = uuid.uuid4()
    bidding.fail_status = 503

    async with connect(f"{realtime_url}/ws/listings/{listing_id}") as ws:
        with suppress(ConnectionClosed):
            await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT)

    for _ in range(100):
        if hub.watcher_count(auction_channel(listing_id)) == 0:
            break
        await asyncio.sleep(0.02)
    assert hub.watcher_count(auction_channel(listing_id)) == 0


async def test_many_watchers_of_one_auction_share_a_single_subscription(
    bidding: FakeBidding, realtime_url: str
) -> None:
    listing_id = uuid.uuid4()
    bidding.put(auction_state(listing_id, version=0, price="100.00"))
    channel = auction_channel(listing_id)

    async with AsyncExitStack() as stack:
        sockets = [
            await stack.enter_async_context(connect(f"{realtime_url}/ws/listings/{listing_id}"))
            for _ in range(3)
        ]
        for ws in sockets:
            await receive(ws)  # the snapshot means the viewer has joined
        assert hub.watcher_count(channel) == 3

    # Every socket has closed. The hub should have let go of the channel.
    for _ in range(100):
        if hub.watcher_count(channel) == 0:
            break
        await asyncio.sleep(0.02)
    assert hub.watcher_count(channel) == 0


async def test_a_viewer_that_stops_reading_does_not_hold_back_another(
    bidding: FakeBidding, realtime_url: str
) -> None:
    listing_id = uuid.uuid4()
    bidding.put(auction_state(listing_id, version=0, price="100.00"))

    async with (
        connect(f"{realtime_url}/ws/listings/{listing_id}") as stalled,
        connect(f"{realtime_url}/ws/listings/{listing_id}") as healthy,
    ):
        await receive(healthy)  # its snapshot
        # `stalled` never reads again, so its messages pile up on its side.

        forwarder = Forwarder()
        for i in range(1, 21):
            await forwarder.handle(update_bytes(listing_id, version=i, price=f"{100 + 5 * i}.00"))

        for expected in range(1, 21):
            assert (await receive(healthy))["version"] == expected

        # Still open: twenty small messages fit in the socket buffers, so nothing was dropped yet.
        assert stalled.close_code is None


async def test_a_reconnecting_viewer_gets_one_fresh_snapshot_and_no_replay(
    bidding: FakeBidding, realtime_url: str
) -> None:
    listing_id = uuid.uuid4()
    bidding.put(auction_state(listing_id, version=0, price="100.00"))

    async with connect(f"{realtime_url}/ws/listings/{listing_id}") as first:
        assert (await receive(first))["current_price"] == "100.00"

    # Nobody is watching while the price moves. The viewer has to catch up on reconnect.
    bidding.put(auction_state(listing_id, version=2, price="125.00"))
    await Forwarder().handle(update_bytes(listing_id, version=2, price="125.00"))  # to no one

    async with connect(f"{realtime_url}/ws/listings/{listing_id}") as second:
        snapshot = await receive(second)
        assert snapshot["current_price"] == "125.00"
        assert snapshot["version"] == 2
        await assert_silent(second)  # nothing from the first connection is replayed


async def test_a_settled_status_reaches_the_watcher(
    bidding: FakeBidding, realtime_url: str
) -> None:
    listing_id = uuid.uuid4()
    bidding.put(auction_state(listing_id, version=2, price="150.00"))

    async with connect(f"{realtime_url}/ws/listings/{listing_id}") as ws:
        assert (await receive(ws))["status"] == "live"

        await Forwarder().handle(update_bytes(listing_id, version=3, price="150.00", status="sold"))
        final = await receive(ws)

    assert final["status"] == "sold"
    assert "winner" not in json.dumps(final)


@pytest.fixture
async def separate_realtime(bidding: FakeBidding) -> AsyncIterator[str]:
    """A second realtime process sharing this Redis, like a second container.

    Yields its WebSocket base URL. Updates are published in the test process and reach
    this process only through Redis, so the test proves the fan-out crosses processes.
    """
    port = free_port()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
        cwd=SERVICE_DIR,
        env={**os.environ, "BIDDING_URL": bidding.url},
    )
    try:
        async with httpx.AsyncClient() as probe:
            for _ in range(200):
                with suppress(httpx.TransportError):
                    response = await probe.get(f"http://127.0.0.1:{port}/healthz")
                    if response.status_code == 200:
                        break
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("the second realtime process never became healthy")
        yield f"ws://127.0.0.1:{port}"
    finally:
        process.terminate()
        await process.wait()


async def test_an_update_published_in_one_process_reaches_a_viewer_in_another(
    bidding: FakeBidding, separate_realtime: str
) -> None:
    listing_id = uuid.uuid4()
    bidding.put(auction_state(listing_id, version=0, price="100.00"))

    async with connect(f"{separate_realtime}/ws/listings/{listing_id}") as ws:
        assert (await receive(ws))["current_price"] == "100.00"

        await Forwarder().handle(update_bytes(listing_id, version=1, price="125.00"))
        update = await receive(ws)

    assert update["listing_id"] == str(listing_id)
    assert update["current_price"] == "125.00"
