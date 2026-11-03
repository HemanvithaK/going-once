"""Stand-ins and helpers shared by the realtime tests."""

import asyncio
import socket
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import uvicorn
from auction_contracts.events import AuctionStatus, AuctionUpdated
from fastapi import FastAPI, Response


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[int]:
    """Run an app on a local port, in this process. Yields the port."""
    port = free_port()
    # lifespan is off: the test session already owns the Redis client.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()  # surface a startup failure instead of waiting forever
        await asyncio.sleep(0.01)
    try:
        yield port
    finally:
        server.should_exit = True
        await task


def auction_state(
    listing_id: uuid.UUID,
    *,
    version: int,
    price: str,
    status: str = "live",
) -> AuctionUpdated:
    return AuctionUpdated(
        listing_id=listing_id,
        version=version,
        status=AuctionStatus(status),
        current_price=Decimal(price),
        ends_at=datetime.now(UTC) + timedelta(hours=1),
    )


def update_bytes(listing_id: uuid.UUID, *, version: int, price: str, status: str = "live") -> bytes:
    """An auction-updates message, as bidding would put it on the topic."""
    state = auction_state(listing_id, version=version, price=price, status=status)
    return state.model_dump_json().encode()


class FakeBidding:
    """A tiny bidding service that serves only the state endpoint, with knobs to break it."""

    def __init__(self) -> None:
        self.url = ""
        self.states: dict[str, AuctionUpdated] = {}
        self.fail_status: int | None = None  # answer every request with this status
        self.raw_body: str | None = None  # answer with exactly this body
        self.delay_seconds = 0.0  # wait before answering
        self.app = FastAPI()
        self.app.add_api_route("/listings/{listing_id}/state", self._state, methods=["GET"])

    def put(self, state: AuctionUpdated) -> None:
        self.states[str(state.listing_id)] = state

    async def _state(self, listing_id: str) -> Response:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail_status is not None:
            return Response(status_code=self.fail_status)
        if self.raw_body is not None:
            return Response(content=self.raw_body, media_type="application/json")
        found = self.states.get(listing_id)
        if found is None:
            return Response(status_code=404)
        return Response(content=found.model_dump_json(), media_type="application/json")
