from functools import lru_cache

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

    # Where listing-events comes from.
    kafka_bootstrap_servers: str = "localhost:9092"


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once, not on every import."""
    return Settings()  # type: ignore[call-arg]
