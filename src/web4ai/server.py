"""Uvicorn entrypoints for local dev and container production."""

from __future__ import annotations

import uvicorn

from web4ai.settings import ServerSettings

APP_IMPORT = "web4ai.api.app:app"


def serve(settings: ServerSettings | None = None) -> None:
    cfg = settings or ServerSettings()
    uvicorn.run(
        APP_IMPORT,
        host=cfg.host,
        port=cfg.port,
        reload=cfg.reload,
        factory=False,
    )
