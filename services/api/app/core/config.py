from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every knob the service has, read from the environment once at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    log_level: str = "INFO"

    database_url: str
    redis_url: str

    jwt_secret: str
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