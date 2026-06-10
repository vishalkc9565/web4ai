"""Caching wrapper around the hybrid extractor."""

from __future__ import annotations

from datetime import UTC, datetime

from web4ai.cache.store import CacheStore, cache_key, get_default_store, options_digest
from web4ai.extractor.hybrid import HybridExtractor
from web4ai.models import ExtractionResponse, ExtractOptions


class CachedExtractor:
    def __init__(
        self,
        inner: HybridExtractor | None = None,
        store: CacheStore | None = None,
    ) -> None:
        self._inner = inner or HybridExtractor()
        self._store = store or get_default_store()

    async def extract(self, options: ExtractOptions) -> ExtractionResponse:
        if not options.use_cache:
            return await self._inner.extract(options)

        # First pass without cache to get content hash — we need HTML for the key.
        # For cache lookup we key on URL + options only when we don't have HTML yet;
        # after extraction we store with content hash for idempotency.
        response = await self._inner.extract(options)

        digest = options_digest(
            {
                "render": options.render,
                "actions": options.actions,
                "include_actions": options.include_actions,
                "max_tokens": options.max_tokens,
                "selector_hint": options.selector_hint,
            }
        )

        # Re-check cache with content-derived key if we have markdown
        if response.markdown and not response.error:
            key = cache_key(str(options.url), response.markdown, digest)
            cached = self._store.get(key)
            if cached is not None:
                cached.meta.cached = True
                cached.meta.cached_at = cached.meta.fetched_at
                cached.meta.strategy = "cache"
                return cached
            self._store.set(key, response, options.cache_ttl)

        return response

    async def extract_with_cache_check(self, options: ExtractOptions) -> ExtractionResponse:
        """Extract with URL-level cache shortcut before network fetch."""
        if not options.use_cache or options.cache_ttl <= 0:
            return await self._inner.extract(options)

        digest = options_digest(
            {
                "render": options.render,
                "actions": options.actions,
                "include_actions": options.include_actions,
                "max_tokens": options.max_tokens,
                "selector_hint": options.selector_hint,
            }
        )
        # URL-only probe key for fast hits when content unchanged
        probe_key = cache_key(str(options.url), "", digest)
        cached = self._store.get(probe_key)
        if cached is not None:
            hit = cached.model_copy(deep=True)
            hit.meta.cached = True
            hit.meta.cached_at = datetime.now(UTC)
            hit.meta.strategy = "cache"
            return hit

        response = await self._inner.extract(options)
        if response.markdown:
            store_key = cache_key(str(options.url), response.markdown, digest)
            self._store.set(store_key, response.model_copy(deep=True), options.cache_ttl)
            self._store.set(probe_key, response.model_copy(deep=True), options.cache_ttl)
        return response
