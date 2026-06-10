"""Pydantic models for extraction requests and responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class ExtractionError(BaseModel):
    code: str
    message: str
    recoverable: bool = False


class ExtractOptions(BaseModel):
    url: HttpUrl
    render: Literal["auto", "always", "never"] = "auto"
    actions: Literal["none", "fast", "verified"] = "fast"
    include_actions: bool = True
    max_tokens: int | None = Field(default=None, ge=100)
    selector_hint: str | None = None
    use_cache: bool = True
    cache_ttl: int = Field(default=3600, ge=0)
    timeout_ms: int = Field(default=30000, ge=1000, le=60000)
    js_wait_selector: str | None = None


class ActionItem(BaseModel):
    type: Literal["link", "form", "button"]
    label: str
    target: str | None = None
    method: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    description: str


class ExtractionMeta(BaseModel):
    url: str
    final_url: str
    fetched_at: datetime
    extraction_path: Literal["static", "render"] = "static"
    strategy: Literal["static", "render", "render_with_proxy", "cache"] = "static"
    truncated: bool = False
    cached: bool = False
    cached_at: datetime | None = None
    tokens: dict[str, int] = Field(default_factory=dict)
    timings_ms: dict[str, float] = Field(default_factory=dict)


class ExtractionResponse(BaseModel):
    markdown: str = ""
    actions: list[ActionItem] = Field(default_factory=list)
    meta: ExtractionMeta
    error: ExtractionError | None = None
