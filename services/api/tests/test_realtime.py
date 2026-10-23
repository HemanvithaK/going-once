import asyncio
import json
import socket
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, suppress
from datetime import timedelta

import httpx
import pytest
import uvicorn
from sqlalchemy import func, update
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.core.db import SessionLocal
from app.core.events import listing_channel
from app.core.listing_hub import listing_hub
from app.main import app
from app.models.listing import Listing
from app.services.closing import close_all_due
from tests.helpers import create_listing, create_live_listing, make_user, place_bid

RECEIVE_TIMEOUT = 3.0
QUIET_PERIOD = 0.5


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


async def set_deadline_past(listing_id: str) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(
            update(Listing)
            .where(Listing.id == uuid.UUID(listing_id))
            .values(ends_at=func.clock_timestamp() - timedelta(seconds=1))
        )


@pytest.fixture
async def server_url() -> AsyncIterator[str]:
    """Run the real app on a local port, so WebSocket clients can connect to it."""
    port = free_port()

    # lifespan is off: the test session already owns the engine and Redis client.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()  # surface a startup failure instead of waiting forever
        await asyncio.sleep(0.01)

    yield f"ws://127.0.0.1:{port}"

    server.should_exit = True
    await task


async def _wait_for_health(probe: httpx.AsyncClient, base_url: str) -> None:
    for _ in range(200):
        with suppress(httpx.TransportError):
            response = await probe.get(f"{base_url}/healthz")
            if response.status_code == 200:
                return
        await asyncio.sleep(0.05)
    raise RuntimeError(f"{base_url} never became healthy")


async def _start_api(port: int) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
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
    )


@pytest.fixture
async def two_api_instances() -> AsyncIterator[tuple[str, str]]:
    """Two separate API processes sharing one database and one Redis, like two containers.

    Yields (http url of instance A, websocket url of instance B).
    """
    port_a, port_b = free_port(), free_port()
    processes = [await _start_api(port_a), await _start_api(port_b)]
    try:
        async with httpx.AsyncClient() as probe:
            await _wait_for_health(probe, f"http://127.0.0.1:{port_a}")
            await _wait_for_health(probe, f"http://127.0.0.1:{port_b}")
        yield f"http://127.0.0.1:{port_a}", f"ws://127.0.0.1:{port_b}"
    finally:
        for process in processes:
            process.terminate()
            await process.wait()


async def test_many_watchers_of_one_listing_share_a_single_subscription(
    client: httpx.AsyncClient, server_url: str
) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    channel = listing_channel(lid)

    async with AsyncExitStack() as stack:
        sockets = [
            await stack.enter_async_context(connect(f"{server_url}/ws/listings/{lid}"))
            for _ in range(3)
        ]
        for ws in sockets:
            await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT)  # snapshot = joined
        assert listing_hub.watcher_count(channel) == 3

    # Every socket has closed. The hub should have let go of the channel.
    for _ in range(100):
        if listing_hub.watcher_count(channel) == 0:
            break
        await asyncio.sleep(0.02)
    assert listing_hub.watcher_count(channel) == 0


async def test_a_watcher_gets_the_current_state_on_connect(
    client: httpx.AsyncClient, server_url: str
) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00")

    async with connect(f"{server_url}/ws/listings/{listing['id']}") as ws:
        snapshot = json.loads(await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT))

    assert snapshot["type"] == "listing.updated"
    assert snapshot["listing_id"] == listing["id"]
    assert snapshot["status"] == "live"
    assert snapshot["current_price"] == "100.00"


async def test_a_bid_is_pushed_to_a_connected_watcher(
    client: httpx.AsyncClient, server_url: str
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]
    await place_bid(client, lid, alice, "120.00", "ws-push-alice")

    async with connect(f"{server_url}/ws/listings/{lid}") as ws:
        snapshot = json.loads(await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT))
        assert snapshot["current_price"] == "100.00"

        # Bob's bid of 150 beats Alice's 120 ceiling, so the price moves to 125.
        response = await place_bid(client, lid, bob, "150.00", "ws-push-bob")
        assert response.status_code == 201, response.text

        update_message = json.loads(await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT))

    assert update_message["current_price"] == "125.00"
    assert update_message["status"] == "live"


async def test_a_bid_accepted_by_one_instance_reaches_a_viewer_on_another(
    client: httpx.AsyncClient, two_api_instances: tuple[str, str]
) -> None:
    api_url, ws_url = two_api_instances
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]
    await place_bid(client, lid, alice, "120.00", "xinst-alice")

    async with connect(f"{ws_url}/ws/listings/{lid}") as ws:  # viewer on instance B
        await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT)  # snapshot

        async with httpx.AsyncClient(base_url=api_url) as remote:  # bid on instance A
            response = await place_bid(remote, lid, bob, "150.00", "xinst-bob")
        assert response.status_code == 201, response.text

        update_message = json.loads(await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT))

    assert update_message["listing_id"] == lid
    assert update_message["current_price"] == "125.00"


async def test_a_rejected_bid_sends_nothing_to_a_watcher(
    client: httpx.AsyncClient, server_url: str
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]
    await place_bid(client, lid, alice, "120.00", "reject-alice")

    async with connect(f"{server_url}/ws/listings/{lid}") as ws:
        await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT)  # snapshot

        # The minimum is 105, so 102 is refused and the transaction rolls back.
        response = await place_bid(client, lid, bob, "102.00", "reject-bob")
        assert response.status_code == 409

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.recv(), timeout=QUIET_PERIOD)


async def test_closing_an_auction_sends_its_final_status_to_watchers(
    client: httpx.AsyncClient, server_url: str
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    await place_bid(client, lid, alice, "150.00", "final-alice")
    await set_deadline_past(lid)

    async with connect(f"{server_url}/ws/listings/{lid}") as ws:
        snapshot = json.loads(await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT))
        assert snapshot["status"] == "live"

        assert await close_all_due() == 1
        final = json.loads(await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT))

    assert final["listing_id"] == lid
    assert final["status"] == "sold"


async def test_a_viewer_that_stops_reading_does_not_hold_back_another(
    client: httpx.AsyncClient, server_url: str
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]

    async with (
        connect(f"{server_url}/ws/listings/{lid}") as stalled,
        connect(f"{server_url}/ws/listings/{lid}") as healthy,
    ):
        await asyncio.wait_for(healthy.recv(), timeout=RECEIVE_TIMEOUT)  # its snapshot
        # `stalled` never reads again, so its messages pile up on its side.

        # Alice raises her own ceiling 20 times. Each bid is one message to both viewers.
        for i in range(20):
            response = await place_bid(client, lid, alice, f"{120 + 5 * i}.00", f"healthy-{i:03d}")
            assert response.status_code == 201, response.text

        for _ in range(20):
            await asyncio.wait_for(healthy.recv(), timeout=RECEIVE_TIMEOUT)

        # Still open: twenty small messages fit in the socket buffers, so nothing was dropped yet.
        assert stalled.close_code is None


async def test_a_reconnecting_viewer_gets_one_fresh_snapshot_and_no_replay(
    client: httpx.AsyncClient, server_url: str
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]
    await place_bid(client, lid, alice, "120.00", "reconnect-alice")

    async with connect(f"{server_url}/ws/listings/{lid}") as first:
        first_snapshot = json.loads(await asyncio.wait_for(first.recv(), timeout=RECEIVE_TIMEOUT))
        assert first_snapshot["current_price"] == "100.00"

    # Nobody is watching while Bob bids. The viewer has to catch up on reconnect.
    await place_bid(client, lid, bob, "150.00", "reconnect-bob")

    async with connect(f"{server_url}/ws/listings/{lid}") as second:
        snapshot = json.loads(await asyncio.wait_for(second.recv(), timeout=RECEIVE_TIMEOUT))
        assert snapshot["current_price"] == "125.00"

        # One snapshot, then silence: nothing from the first connection is replayed.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(second.recv(), timeout=QUIET_PERIOD)


async def test_an_unknown_listing_is_closed_with_a_not_found_code(server_url: str) -> None:
    async with connect(f"{server_url}/ws/listings/{uuid.uuid4()}") as ws:
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT)

    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == 4404


async def test_a_draft_listing_looks_missing_to_watchers(
    client: httpx.AsyncClient, server_url: str
) -> None:
    seller = await make_user(client, "seller@example.com")
    draft = await create_listing(client, seller)

    async with connect(f"{server_url}/ws/listings/{draft['id']}") as ws:
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT)

    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == 4404