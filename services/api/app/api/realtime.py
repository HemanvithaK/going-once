"""Live auction updates over WebSocket.

A client connects to /ws/listings/{id}. It receives the listing's current
state right away, then every change as it happens. Watching needs no login:
the data is public, and bids are never sent over this socket.
"""

import asyncio
import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.db import SessionLocal
from app.core.events import listing_channel, listing_snapshot
from app.core.listing_hub import listing_hub
from app.core.logging import get_logger
from app.models.listing import Listing, ListingStatus

log = get_logger(__name__)
router = APIRouter(tags=["realtime"])

# Close codes 4000-4999 belong to the application.
CLOSE_NOT_FOUND = 4404  # no such listing, or a draft
CLOSE_TOO_SLOW = 4008  # the viewer fell too far behind to keep up


@router.websocket("/ws/listings/{listing_id}")
async def watch_listing(websocket: WebSocket, listing_id: uuid.UUID) -> None:
    await websocket.accept()
    channel = listing_channel(listing_id)

    # 1. Join before reading the snapshot. Anything published after the join is
    #    queued for us, so nothing falls between the snapshot and the stream.
    #    It may repeat the snapshot, which is harmless: every message is full state.
    subscriber = await listing_hub.join(channel)
    try:
        async with SessionLocal() as session:
            listing = await session.get(Listing, listing_id)
        if listing is None or listing.status is ListingStatus.DRAFT:
            # Drafts are private to the seller, so they look like missing listings.
            await websocket.close(code=CLOSE_NOT_FOUND)
            return
        await websocket.send_text(json.dumps(listing_snapshot(listing)))

        # 2. Send queued messages until the client leaves or we drop it. The
        #    client-side watcher runs alongside, so a quiet auction still notices
        #    a disconnect.
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
                    # The hub dropped us because our queue overflowed. The client
                    # should reconnect, and its new snapshot brings it up to date.
                    await websocket.close(code=CLOSE_TOO_SLOW)
                    return
                await websocket.send_text(data)
        finally:
            client_left.cancel()
    except WebSocketDisconnect:
        log.info("socket_closed", listing_id=str(listing_id))
    finally:
        await listing_hub.leave(channel, subscriber)


async def _wait_until_client_leaves(websocket: WebSocket) -> None:
    """Read and discard anything the client sends. Returns when it disconnects."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return