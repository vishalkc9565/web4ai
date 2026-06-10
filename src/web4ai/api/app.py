"""FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from web4ai.api.routes import extract
from web4ai.extractor.cached import CachedExtractor
from web4ai.extractor.hybrid import HybridExtractor
from web4ai.pipeline.render import get_renderer


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.extractor = CachedExtractor(HybridExtractor())
    yield
    try:
        await get_renderer().close()
    except Exception:
        pass


app = FastAPI(
    title="web4AI",
    description="Extract markdown and action sets from any URL for AI agents.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(extract.router)

# Shorter path for quick onboarding (same handler as /v1/extract)
app.add_api_route(
    "/extract",
    extract.extract_url,
    methods=["POST"],
    tags=["extract"],
    include_in_schema=True,
)


@app.get("/health")
async def health():
    return {"status": "ok"}
