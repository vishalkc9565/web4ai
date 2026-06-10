"""In-memory extraction cache with TTL.

Swap to Redis in production by implementing the CacheStore protocol:

    class RedisCacheStore:
        async def get(self, key: str) -> bytes | None: ...
        async def set(self, key: str, value: bytes, ttl_seconds: int) -> None: ...
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Protocol

from web4ai.models import ExtractionResponse


@dataclass
class CacheEntry:
    value: ExtractionResponse
    expires_at: float


class CacheStore(Protocol):
    def get(self, key: str) -> ExtractionResponse | None: ...
    def set(self, key: str, value: ExtractionResponse, ttl_seconds: int) -> None: ...


class InMemoryCacheStore:
    def __init__(self) -> None:
        self._data: dict[str, CacheEntry] = {}

    def get(self, key: str) -> ExtractionResponse | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            del self._data[key]
            return None
        return entry.value

    def set(self, key: str, value: ExtractionResponse, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        self._data[key] = CacheEntry(value=value, expires_at=time.time() + ttl_seconds)


def content_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()


def cache_key(url: str, html: str, options_digest: str) -> str:
    return f"{url}|{content_hash(html)}|{options_digest}"


def options_digest(options: dict) -> str:
    canonical = json.dumps(options, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


_default_store = InMemoryCacheStore()


def get_default_store() -> InMemoryCacheStore:
    return _default_store
