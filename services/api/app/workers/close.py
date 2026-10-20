"""The closer: a long-running process that closes auctions when they are due.

Run one or more copies. They never talk to each other. They coordinate only
through the database's row locks, so adding a copy is always safe.
"""

import argparse
import asyncio

from app.core.config import get_settings
from app.core.db import engine
from app.core.logging import configure_logging, get_logger
from app.services.closing import close_all_due

log = get_logger(__name__)


async def run_forever(poll_seconds: float) -> None:
    while True:
        try:
            closed = await close_all_due()
            if closed:
                log.info("closer_cycle_done", closed=closed)
        except Exception:
            # One bad cycle must not kill the process. Log it and try again next poll.
            log.exception("closer_cycle_failed")
        await asyncio.sleep(poll_seconds)


async def run_once() -> int:
    try:
        return await close_all_due()
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Close auctions whose deadline has passed.")
    parser.add_argument("--once", action="store_true", help="close what is due, then exit")
    args = parser.parse_args()

    configure_logging()
    if args.once:
        closed = asyncio.run(run_once())
        print(f"closed {closed} auction(s)")
    else:
        asyncio.run(run_forever(get_settings().closer_poll_seconds))


if __name__ == "__main__":
    main()