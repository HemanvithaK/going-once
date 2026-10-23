import os

import pytest

from app.core.kafka import kafka_reachable

requires_broker = pytest.mark.skipif(
    not os.environ.get("KAFKA_BOOTSTRAP_SERVERS"),
    reason="needs the Kafka broker from docker compose (KAFKA_BOOTSTRAP_SERVERS is not set)",
)


async def test_a_closed_port_is_reported_as_unreachable_without_raising() -> None:
    # Port 1 has no listener, so the connection is refused straight away.
    assert await kafka_reachable("127.0.0.1:1") is False


@requires_broker
async def test_the_compose_broker_is_reachable() -> None:
    assert await kafka_reachable() is True