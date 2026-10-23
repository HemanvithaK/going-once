"""Test stand-in for Kafka: the relay sends into a list, and the forwarder reads from it.

It runs the same relay and forwarder code as production, minus the broker. The
broker-gated test in test_consumer.py covers the real one.
"""

from app.workers.consumer import Forwarder
from app.workers.relay import relay_once


class LoopbackBus:
    """Collects what the relay would have sent to Kafka."""

    def __init__(self) -> None:
        self.values: list[bytes] = []

    async def send_and_wait(self, topic: str, *, key: bytes, value: bytes) -> object:
        self.values.append(value)
        return None


async def deliver_outbox(forwarder: Forwarder | None = None) -> None:
    """Relay every unsent outbox row, then forward it to Redis, as production would."""
    bus = LoopbackBus()
    while await relay_once(bus):
        pass
    forwarder = forwarder or Forwarder()
    for value in bus.values:
        await forwarder.handle(value)