from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every knob the service has, read from the environment once at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    log_level: str = "INFO"

    database_url: str
    redis_url: str

    # RFC 7518: an HMAC-SHA256 key should be at least 32 bytes.
    # Generate one with: openssl rand -hex 32
    jwt_secret: str = Field(min_length=32)
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    # Connection pool. pool_size is the steady-state number of open
    # connections; max_overflow is how many extra we may open under burst.
    db_pool_size: int = 10
    db_max_overflow: int = 20


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once, not on every import."""
    return Settings()  # type: ignore[call-arg]
