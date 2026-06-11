"""Runtime configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    """API server bind options (override via WEB4AI_* env vars)."""

    model_config = SettingsConfigDict(env_prefix="WEB4AI_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False

    @classmethod
    def for_dev(cls) -> ServerSettings:
        return cls(host="0.0.0.0", port=8000, reload=True)

    @classmethod
    def for_container(cls) -> ServerSettings:
        # Cloudflare Containers expect the app on the internal bind address.
        return cls(host="10.0.0.1", port=8080, reload=False)
