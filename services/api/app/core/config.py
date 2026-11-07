from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every knob the service has, read from the environment once at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    log_level: str = "INFO"

    database_url: str

    # RFC 7518: an HMAC-SHA256 key should be at least 32 bytes.
    # Generate one with: openssl rand -hex 32
    jwt_secret: str = Field(min_length=32)
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    # Connection pool. pool_size is the steady-state number of open
    # connections; max_overflow is how many extra we may open under burst.
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # Kafka broker list, "host:port" separated by commas. The relay and the feed
    # consumers use it.
    kafka_bootstrap_servers: str = "localhost:9092"

    # Browser origins allowed to call this service: the frontend, comma-separated.
    # Each origin is listed exactly. "*" is refused, because it would let any website
    # call this service from a visitor's browser.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard_origin(cls, value: str) -> str:
        if any(origin.strip() == "*" for origin in value.split(",")):
            raise ValueError("cors_origins must list exact origins, not *")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        """The origins as a list, without spaces, empty entries or trailing slashes."""
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once, not on every import."""
    return Settings()  # type: ignore[call-arg]
