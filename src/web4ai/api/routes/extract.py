"""POST /v1/extract endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, HttpUrl

from web4ai.models import ExtractOptions

router = APIRouter(prefix="/v1", tags=["extract"])


class ExtractRequestBody(BaseModel):
    url: HttpUrl
    render: str = "auto"
    actions: str = "fast"
    include_actions: bool = True
    max_tokens: int | None = None
    selector_hint: str | None = None
    use_cache: bool = True
    cache_ttl: int = Field(default=3600, ge=0)
    timeout_ms: int = Field(default=30000, ge=1000, le=60000)
    js_wait_selector: str | None = None


@router.post("/extract")
async def extract_url(body: ExtractRequestBody, request: Request, response: Response):
    options = ExtractOptions(
        url=body.url,
        render=body.render,  # type: ignore[arg-type]
        actions=body.actions,  # type: ignore[arg-type]
        include_actions=body.include_actions,
        max_tokens=body.max_tokens,
        selector_hint=body.selector_hint,
        use_cache=body.use_cache,
        cache_ttl=body.cache_ttl,
        timeout_ms=body.timeout_ms,
        js_wait_selector=body.js_wait_selector,
    )
    extractor = request.app.state.extractor
    result = await extractor.extract_with_cache_check(options)

    if result.meta.cached:
        response.headers["X-Cache"] = "HIT"
        response.headers["Cache-Control"] = f"max-age={body.cache_ttl}"
    else:
        response.headers["X-Cache"] = "MISS"

    return result.model_dump(mode="json")
