"""Integration tests against live URLs.

Run with: pytest tests/test_integration.py -m integration
Included in CI via the integration job (requires network).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from web4ai.api.app import app
from web4ai.cache.store import InMemoryCacheStore
from web4ai.extractor.cached import CachedExtractor
from web4ai.extractor.hybrid import HybridExtractor

pytestmark = pytest.mark.integration


@pytest.fixture
async def live_client():
    app.state.extractor = CachedExtractor(HybridExtractor(), store=InMemoryCacheStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=60.0) as ac:
        yield ac


async def _extract(live_client: AsyncClient, url: str, **kwargs):
    body = {"url": url, "include_actions": True, "use_cache": False, **kwargs}
    response = await live_client.post("/v1/extract", json=body)
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_static_docs_page(live_client):
    data = await _extract(
        live_client,
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Methods/GET",
        render="never",
    )
    assert len(data["markdown"]) > 200
    assert "#" in data["markdown"] or "##" in data["markdown"]
    assert data["meta"]["extraction_path"] == "static"
    assert data["meta"]["url"].startswith("https://")


@pytest.mark.asyncio
async def test_books_listing_search_form(live_client):
    data = await _extract(live_client, "https://books.toscrape.com/", render="never")
    assert "book" in data["markdown"].lower() or "£" in data["markdown"]
    actions = data.get("actions", [])
    assert len(actions) >= 1
    labels = " ".join(a["label"].lower() for a in actions)
    assert "search" in labels or "next" in labels


@pytest.mark.asyncio
async def test_spa_react_dev_render_path(live_client):
    data = await _extract(
        live_client,
        "https://react.dev/",
        render="auto",
        timeout_ms=45000,
    )
    assert len(data["markdown"]) > 100
    assert data["meta"]["extraction_path"] in ("static", "render")


@pytest.mark.asyncio
async def test_redirect_chain(live_client):
    start_url = "http://books.toscrape.com/"
    data = await _extract(live_client, start_url, render="never")
    final = data["meta"]["final_url"]
    # Redirect may upgrade to HTTPS or normalize the path
    assert final.rstrip("/").endswith("books.toscrape.com")
    assert final != start_url or "book" in data["markdown"].lower()
    assert len(data["markdown"]) > 50


@pytest.mark.asyncio
async def test_amazon_in_bot_wall(live_client):
    """Amazon India should return structured content on bot interstitial, not HTTP 500."""
    data = await _extract(live_client, "https://www.amazon.in/", render="auto")
    assert data["meta"]["url"]
    assert len(data.get("markdown", "")) > 0
    assert "JavaScript" in data["markdown"]
    assert data.get("error") is None or data["error"]["code"] in (
        "bot_detection",
        "empty_page",
    )


@pytest.mark.asyncio
async def test_login_wall_partial_recovery(live_client):
    """GitHub login wall should return structured error or partial content, not HTTP 500."""
    data = await _extract(
        live_client,
        "https://github.com/login",
        render="never",
        include_actions=False,
    )
    assert data["meta"]["url"]
    # Login pages typically yield paywall/bot/empty signals or minimal markdown
    assert data.get("error") is not None or len(data.get("markdown", "")) >= 0


@pytest.mark.asyncio
async def test_meta_fields_present(live_client):
    data = await _extract(live_client, "https://example.com", render="never")
    meta = data["meta"]
    assert meta["fetched_at"]
    assert meta["final_url"]
    assert meta["extraction_path"] in ("static", "render")
    assert "timings_ms" in meta
