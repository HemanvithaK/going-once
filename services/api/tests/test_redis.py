from app.core.redis import redis_ping


async def test_redis_answers_ping() -> None:
    assert await redis_ping() is True