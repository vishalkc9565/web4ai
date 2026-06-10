from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from web4ai.cache.store import InMemoryCacheStore, cache_key, content_hash
from web4ai.extractor.cached import CachedExtractor
from web4ai.models import ExtractionMeta, ExtractionResponse, ExtractOptions


def _response(markdown: str = "hello") -> ExtractionResponse:
    return ExtractionResponse(
        markdown=markdown,
        meta=ExtractionMeta(
            url="https://example.com",
            final_url="https://example.com",
            fetched_at=datetime.now(UTC),
        ),
    )


@pytest.mark.asyncio
async def test_cache_hit_on_second_request():
    store = InMemoryCacheStore()
    inner = AsyncMock()
    inner.extract = AsyncMock(return_value=_response("cached content"))
    extractor = CachedExtractor(inner=inner, store=store)  # type: ignore[arg-type]

    opts = ExtractOptions(url="https://example.com", use_cache=True, cache_ttl=3600)
    r1 = await extractor.extract_with_cache_check(opts)
    r2 = await extractor.extract_with_cache_check(opts)

    assert r1.meta.cached is False
    assert r2.meta.cached is True
    assert inner.extract.await_count == 1


def test_content_hash_stable():
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abcd")


def test_cache_key_includes_options():
    k1 = cache_key("https://x.com", "html", "opt1")
    k2 = cache_key("https://x.com", "html", "opt2")
    assert k1 != k2
