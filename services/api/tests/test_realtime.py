import asyncio
import json
import socket
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

import httpx
import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.core.events import listing_channel
from app.core.listing_hub import listing_hub
from app.main import app
from tests.helpers import create_listing, create_live_listing, make_user, place_bid

RECEIVE_TIMEOUT = 3.0


@pytest.fixture
async def server_url() -> AsyncIterator[str]:
    """Run the real app on a local port, so WebSocket clients can connect to it."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

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

        update = json.loads(await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT))

    assert update["current_price"] == "125.00"
    assert update["status"] == "live"


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