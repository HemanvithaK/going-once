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
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.models.listing import Listing, ListingStatus

log = get_logger(__name__)
router = APIRouter(tags=["realtime"])

# Close codes 4000-4999 belong to the application. 4404 means "no such listing".
CLOSE_NOT_FOUND = 4404


@router.websocket("/ws/listings/{listing_id}")
async def watch_listing(websocket: WebSocket, listing_id: uuid.UUID) -> None:
    await websocket.accept()

    pubsub = redis_client.pubsub()
    try:
        # 1. Subscribe before reading the snapshot. An event sent between the two
        #    steps is then received, not lost. It may repeat the snapshot, which
        #    is harmless because every message carries the full current state.
        await pubsub.subscribe(listing_channel(listing_id))

        async with SessionLocal() as session:
            listing = await session.get(Listing, listing_id)
        if listing is None or listing.status is ListingStatus.DRAFT:
            # Drafts are private to the seller, so they look like missing listings.
            await websocket.close(code=CLOSE_NOT_FOUND)
            return
        await websocket.send_text(json.dumps(listing_snapshot(listing)))

        # 2. Forward events until the client leaves. The watcher for the client's
        #    side runs alongside, because a quiet auction would otherwise never
        #    notice a disconnect.
        client_left = asyncio.create_task(_wait_until_client_leaves(websocket))
        try:
            while not client_left.done():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is not None:
                    await websocket.send_text(message["data"])
        finally:
            client_left.cancel()
    except WebSocketDisconnect:
        log.info("socket_closed", listing_id=str(listing_id))
    finally:
        # One subscription per socket for now. Part D replaces this with one
        # shared subscription per listing per instance.
        await pubsub.aclose()


async def _wait_until_client_leaves(websocket: WebSocket) -> None:
    """Read and discard anything the client sends. Returns when it disconnects."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return