from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every knob the bidding service has, read from the environment once at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    log_level: str = "INFO"

    # This service's own database. Nothing else connects to it.
    database_url: str

    # Connection pool: steady-state connections, plus extra ones allowed under burst.
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # Bidding checks the same access tokens the core API issues, so it needs the same key.
    # RFC 7518: an HMAC-SHA256 key should be at least 32 bytes.
    jwt_secret: str = Field(min_length=32)

    # Anti-snipe: a bid this close to the deadline pushes the deadline out.
    anti_snipe_seconds: int = 30

    # Where listing-events comes from, and where outbox events go.
    kafka_bootstrap_servers: str = "localhost:9092"


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once, not on every import."""
    return Settings()  # type: ignore[call-arg]
