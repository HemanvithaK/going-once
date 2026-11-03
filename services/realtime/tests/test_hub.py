import uuid

from app.core.hub import SUBSCRIBER_QUEUE_LIMIT, hub


async def test_a_stalled_viewer_is_dropped_while_a_healthy_one_gets_every_message() -> None:
    channel = f"auction:{uuid.uuid4()}"
    healthy = await hub.join(channel)
    stalled = await hub.join(channel)
    try:
        total = SUBSCRIBER_QUEUE_LIMIT + 5
        received: list[str | None] = []
        for i in range(total):
            hub.deliver(channel, f"msg-{i}")
            # The healthy viewer reads as it goes. The stalled one never reads.
            received.append(healthy.queue.get_nowait())

        assert received == [f"msg-{i}" for i in range(total)]
        assert not healthy.dropped

        assert stalled.dropped
        leftover: list[str | None] = []
        while not stalled.queue.empty():
            leftover.append(stalled.queue.get_nowait())
        assert leftover == [None]  # the backlog was thrown away, leaving only the drop signal
    finally:
        await hub.leave(channel, healthy)
        await hub.leave(channel, stalled)


async def test_the_last_viewer_leaving_clears_the_channel() -> None:
    channel = f"auction:{uuid.uuid4()}"
    first = await hub.join(channel)
    second = await hub.join(channel)
    assert hub.watcher_count(channel) == 2

    await hub.leave(channel, first)
    assert hub.watcher_count(channel) == 1

    await hub.leave(channel, second)
    assert hub.watcher_count(channel) == 0
