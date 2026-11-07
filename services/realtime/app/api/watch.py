"""Live auction updates over WebSocket.

A client connects to /ws/listings/{id}. It receives the auction's current public state
right away, then every change as it happens. Watching needs no login: the data is public,
and bids are never sent over this socket.
"""

import asyncio
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.core.bidding_client import (
    BiddingClient,
    BiddingUnavailableError,
    get_bidding_client,
)
from app.core.channels import auction_channel
from app.core.hub import hub
from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["realtime"])

# Close codes 4000-4999 belong to the application. 1013 is the standard "try again later".
CLOSE_NOT_FOUND = 4404  # bidding does not know this auction
CLOSE_TOO_SLOW = 4008  # the viewer fell too far behind to keep up
CLOSE_TRY_AGAIN = 1013  # no snapshot from bidding, or updates may have been lost in Redis

BiddingDep = Annotated[BiddingClient, Depends(get_bidding_client)]


@router.websocket("/ws/listings/{listing_id}")
async def watch_auction(
    websocket: WebSocket,
    listing_id: uuid.UUID,
    bidding: BiddingDep,
) -> None:
    await websocket.accept()
    channel = auction_channel(listing_id)

    # 1. Join before asking for the snapshot. Anything published after the join is queued
    #    for us, so no change falls between the snapshot and the stream.
    subscriber = await hub.join(channel)
    try:
        try:
            state = await bidding.fetch_state(listing_id)
        except BiddingUnavailableError as exc:
            log.warning("snapshot_unavailable", listing_id=str(listing_id), error=str(exc))
            await websocket.close(code=CLOSE_TRY_AGAIN)
            return
        if state is None:
            await websocket.close(code=CLOSE_NOT_FOUND)
            return
        await websocket.send_text(state.model_dump_json())

        # 2. Send queued messages until the client leaves or we drop it. A message queued
        #    while the snapshot was in flight can be older than the snapshot, and showing
        #    it would move the price backwards. So only a newer version is ever sent.
        newest_sent = state.version
        client_left = asyncio.create_task(_wait_until_client_leaves(websocket))
        try:
            while True:
                next_message = asyncio.create_task(subscriber.queue.get())
                done, _ = await asyncio.wait(
                    {next_message, client_left}, return_when=asyncio.FIRST_COMPLETED
                )
                if next_message not in done:
                    next_message.cancel()
                    return
                data = next_message.result()
                if data is None:
                    # The hub dropped us: either our queue overflowed, or Redis failed and we
                    # may have missed updates. Either way the client should reconnect, and
                    # its new snapshot brings it up to date. Redis being down is worth a
                    # different code, because retrying at once will not help.
                    code = CLOSE_TRY_AGAIN if subscriber.source_lost else CLOSE_TOO_SLOW
                    await websocket.close(code=code)
                    return
                version = _version_of(data)
                if version is None or version <= newest_sent:
                    continue
                await websocket.send_text(data)
                newest_sent = version
        finally:
            client_left.cancel()
    except WebSocketDisconnect:
        log.info("socket_closed", listing_id=str(listing_id))
    finally:
        await hub.leave(channel, subscriber)


def _version_of(data: str) -> int | None:
    """The version inside a queued message, or None if the message is unreadable."""
    try:
        return int(json.loads(data)["version"])
    except (ValueError, KeyError, TypeError):
        log.error("queued_message_unreadable")
        return None


async def _wait_until_client_leaves(websocket: WebSocket) -> None:
    """Read and discard anything the client sends. Returns when it disconnects."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
