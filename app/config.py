from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Cloudflare Turnstile. The secret is read from the TURNSTILE_SECRET
    # environment variable and is never written to source.
    turnstile_secret: str = Field(default="", validation_alias="TURNSTILE_SECRET")
    turnstile_enabled: bool = Field(default=True, validation_alias="TURNSTILE_ENABLED")

    # Session lifetimes. The refresh token carries the long-lived session;
    # the access token is deliberately short so a leaked bearer token has a
    # small blast radius.
    access_token_ttl_minutes: int = Field(default=30, validation_alias="ACCESS_TOKEN_TTL_MINUTES")
    refresh_token_ttl_days: int = Field(default=60, validation_alias="REFRESH_TOKEN_TTL_DAYS")
    # Hard ceiling on a session family regardless of how often it is rotated.
    session_absolute_ttl_days: int = Field(default=90, validation_alias="SESSION_ABSOLUTE_TTL_DAYS")

    database_url: str = Field(
        default="sqlite+aiosqlite:///./egress.db",
        validation_alias="DATABASE_URL",
    )

    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        validation_alias="CORS_ORIGINS",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
