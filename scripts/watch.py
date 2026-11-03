#!/usr/bin/env python3
"""Watch one auction over WebSocket, the way a browser would.

Print everything a viewer receives:
    python scripts/watch.py LISTING_ID

Or check what a viewer receives, and exit 0 only if it all happened:
    python scripts/watch.py LISTING_ID --expect-price 125 --expect-status sold --timeout 150

Needs the websockets package (it is in the api's dev dependencies).
"""

import argparse
import asyncio
import json
import sys
import time
from decimal import Decimal

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

# "Not found yet" and "try again later". A listing that was just published has not reached
# bidding yet, so the first connection can legitimately be refused. Keep trying.
RETRY_CLOSE_CODES = {4404, 1013}


def show(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {message}", flush=True)


async def watch(
    url: str,
    listing_id: str,
    expect_price: str | None,
    expect_status: str | None,
    timeout: float,
) -> int:
    deadline = time.monotonic() + timeout
    expecting = expect_price is not None or expect_status is not None
    saw_price = expect_price is None
    saw_status = expect_status is None
    newest_version = -1

    while time.monotonic() < deadline:
        previous_in_connection = -1
        try:
            async with connect(f"{url}/ws/listings/{listing_id}") as ws:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except TimeoutError:
                        break
                    event = json.loads(raw)
                    show(
                        f"v{event['version']}  {event['status']:<6}  price {event['current_price']}"
                    )

                    version = int(event["version"])
                    if version <= previous_in_connection or version < newest_version:
                        show(f"FAIL: version went backwards (saw {newest_version} before)")
                        return 1
                    previous_in_connection = version
                    newest_version = version

                    if expect_price is not None and Decimal(event["current_price"]) == Decimal(
                        expect_price
                    ):
                        saw_price = True
                    if expect_status is not None and event["status"] == expect_status:
                        saw_status = True
                    if expecting and saw_price and saw_status:
                        show("OK: saw everything expected, with versions only ever going up")
                        return 0
        except ConnectionClosed as closed:
            code = closed.rcvd.code if closed.rcvd is not None else None
            if code not in RETRY_CLOSE_CODES and code != 4008:
                show(f"connection closed with code {code}")
                return 0 if not expecting else 1
            await asyncio.sleep(1)
        except (OSError, InvalidHandshake):
            await asyncio.sleep(1)

    if not expecting:
        return 0
    missing = []
    if not saw_price:
        missing.append(f"price {expect_price}")
    if not saw_status:
        missing.append(f"status {expect_status}")
    show("FAIL: timed out without seeing " + " and ".join(missing))
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch one auction over WebSocket.")
    parser.add_argument("listing_id")
    parser.add_argument("--url", default="ws://localhost:8002", help="the realtime service")
    parser.add_argument("--expect-price", help="succeed only after seeing this price")
    parser.add_argument("--expect-status", help="succeed only after seeing this status")
    parser.add_argument("--timeout", type=float, default=600, help="seconds to keep watching")
    args = parser.parse_args()
    try:
        code = asyncio.run(
            watch(args.url, args.listing_id, args.expect_price, args.expect_status, args.timeout)
        )
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
