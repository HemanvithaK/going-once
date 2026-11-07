"""When Redis drops, viewers must be told, not left looking at a stale price.

Redis pub/sub keeps nothing for a connection that is down. redis-py reconnects and
resubscribes by itself, but anything published during the gap is gone, and nothing
tells the hub. A viewer would keep showing an old price until the next bid. So when the
hub sees Redis fail, it drops every viewer. Each client reconnects and its fresh snapshot
brings it up to date.

The outage here is real: CLIENT KILL closes the hub's own pub/sub connection.
"""

import asyncio
import uuid

import pytest
import websockets
from websockets.exceptions import ConnectionClosed

from app.core.hub import hub
from app.core.redis import redis_client
from tests.fakes import FakeBidding, auction_state

CLOSE_TRY_AGAIN = 1013


async def cut_redis_connections() -> None:
    """Close every pub/sub connection from the server side, as a Redis restart would."""
    killed = await redis_client.client_kill_filter(_type="pubsub")
    assert killed >= 1, "the hub had no pub/sub connection to cut"


async def test_a_redis_failure_drops_every_viewer_and_marks_why() -> None:
    channel = f"auction:{uuid.uuid4()}"
    first = await hub.join(channel)
    second = await hub.join(channel)
    try:
        await cut_redis_connections()

        for viewer in (first, second):
            assert await asyncio.wait_for(viewer.queue.get(), timeout=5) is None
            assert viewer.dropped
            assert viewer.source_lost  # not "too slow": the updates themselves may be missing
    finally:
        await hub.leave(channel, first)
        await hub.leave(channel, second)


async def test_the_hub_keeps_working_after_a_redis_failure() -> None:
    channel = f"auction:{uuid.uuid4()}"
    before = await hub.join(channel)
    await cut_redis_connections()
    assert await asyncio.wait_for(before.queue.get(), timeout=5) is None
    await hub.leave(channel, before)

    after = await hub.join(channel)
    try:
        # Redis may still be reconnecting, so publish until the new viewer sees something.
        for attempt in range(40):
            await redis_client.publish(channel, f"hello-{attempt}")
            try:
                got = await asyncio.wait_for(after.queue.get(), timeout=0.25)
            except TimeoutError:
                continue
            assert got is not None and got.startswith("hello-")
            return
        pytest.fail("the new viewer never received a message after the outage")
    finally:
        await hub.leave(channel, after)


async def test_leaving_does_not_raise_while_redis_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = f"auction:{uuid.uuid4()}"
    viewer = await hub.join(channel)
    assert hub._pubsub is not None

    async def redis_is_down(*args: object) -> None:
        raise ConnectionError("redis is down")

    monkeypatch.setattr(hub._pubsub, "unsubscribe", redis_is_down)
    await hub.leave(channel, viewer)  # must not raise: a socket's cleanup runs this

    assert hub.watcher_count(channel) == 0


async def test_a_socket_is_closed_with_try_again_when_redis_fails_and_reconnects_fresh(
    realtime_url: str, bidding: FakeBidding
) -> None:
    listing_id = uuid.uuid4()
    bidding.put(auction_state(listing_id, version=1, price="100.00"))
    url = f"{realtime_url}/ws/listings/{listing_id}"

    async with websockets.connect(url) as socket:
        assert '"version":1' in await asyncio.wait_for(socket.recv(), timeout=3)

        await cut_redis_connections()
        # An update lands while the connection is down. Bidding has it; this viewer missed it.
        bidding.put(auction_state(listing_id, version=2, price="105.00"))

        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(socket.recv(), timeout=5)
        assert socket.close_code == CLOSE_TRY_AGAIN

    # Reconnecting brings the viewer up to date from a fresh snapshot.
    async with websockets.connect(url) as again:
        snapshot = await asyncio.wait_for(again.recv(), timeout=3)
        assert '"version":2' in snapshot
        assert '"current_price":"105.00"' in snapshot
