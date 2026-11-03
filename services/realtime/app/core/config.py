from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every knob the realtime service has, read from the environment once at startup.

    There is no database setting: this service has no database.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    log_level: str = "INFO"

    # Fan-out between processes: the consumer publishes here, every server process listens.
    redis_url: str

    # Where auction-updates comes from.
    kafka_bootstrap_servers: str = "localhost:9092"

    # A viewer's first snapshot comes from bidding, which owns the auction state.
    # Required, so a missing value fails at startup instead of on the first viewer.
    bidding_url: str
    bidding_timeout_seconds: float = 3.0


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once, not on every import."""
    return Settings()  # type: ignore[call-arg]
