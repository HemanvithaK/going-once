"""Kafka connection helpers.

Nothing in the request path calls Kafka. Bids and closes write their events to
the database (Part B), and the relay sends them to Kafka when it can (Part C).
So an API instance keeps taking bids while Kafka is down.
"""

import asyncio
from contextlib import suppress

from aiokafka import AIOKafkaProducer

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

PING_SECONDS = 5.0


async def kafka_reachable(bootstrap_servers: str | None = None) -> bool:
    """True if a broker answers within a few seconds. Never raises."""
    servers = bootstrap_servers or get_settings().kafka_bootstrap_servers
    producer = AIOKafkaProducer(bootstrap_servers=servers)
    try:
        await asyncio.wait_for(producer.start(), timeout=PING_SECONDS)
        return True
    except Exception as exc:  # a timeout is an Exception too
        log.warning("kafka_ping_failed", servers=servers, error=str(exc) or type(exc).__name__)
        return False
    finally:
        with suppress(Exception):  # stopping a producer that never started may complain
            await producer.stop()