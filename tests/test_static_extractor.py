from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from web4ai.extractor.static import StaticExtractor
from web4ai.models import ExtractOptions
from web4ai.pipeline.fetch import FetchError, FetchResult

FIXTURES = Path(__file__).parent / "fixtures"


def _fetch_result(html: str, url: str = "https://example.com") -> FetchResult:
    return FetchResult(html=html, final_url=url, status_code=200, elapsed_ms=10.0, headers={})


@pytest.mark.asyncio
async def test_static_extract_article():
    html = (FIXTURES / "simple_article.html").read_text()
    with patch("web4ai.extractor.static.fetch_static", AsyncMock(return_value=_fetch_result(html))):
        result = await StaticExtractor().extract(
            ExtractOptions(url="https://example.com", include_actions=True)
        )
    assert "Web Scraping Guide" in result.markdown
    assert result.meta.extraction_path == "static"
    assert result.error is None


@pytest.mark.asyncio
async def test_timeout_returns_structured_error():
    with patch(
        "web4ai.extractor.static.fetch_static",
        AsyncMock(side_effect=FetchError("timeout", "timed out", recoverable=True)),
    ):
        result = await StaticExtractor().extract(ExtractOptions(url="https://example.com"))
    assert result.error is not None
    assert result.error.code == "timeout"
    assert result.error.recoverable


@pytest.mark.asyncio
async def test_redirect_loop_error():
    with patch(
        "web4ai.extractor.static.fetch_static",
        AsyncMock(side_effect=FetchError("redirect_loop", "loop", recoverable=False)),
    ):
        result = await StaticExtractor().extract(ExtractOptions(url="https://example.com"))
    assert result.error.code == "redirect_loop"


@pytest.mark.asyncio
async def test_empty_page_error():
    with patch(
        "web4ai.extractor.static.fetch_static",
        AsyncMock(return_value=_fetch_result("<html><body></body></html>")),
    ):
        result = await StaticExtractor().extract(ExtractOptions(url="https://example.com"))
    assert result.error is not None
    assert result.error.code == "empty_page"


@pytest.mark.asyncio
async def test_paywall_detection():
    html = "<main><p>Subscribe to read this article</p></main>"
    with patch("web4ai.extractor.static.fetch_static", AsyncMock(return_value=_fetch_result(html))):
        result = await StaticExtractor().extract(ExtractOptions(url="https://example.com"))
    assert result.error is not None
    assert result.error.code == "paywall"


@pytest.mark.asyncio
async def test_bot_detection():
    html = "<main><p>Please verify you are human</p></main>"
    with patch(
        "web4ai.extractor.static.fetch_static",
        AsyncMock(return_value=_fetch_result(html, url="https://blocked.example")),
    ):
        result = await StaticExtractor().extract(ExtractOptions(url="https://blocked.example"))
    assert result.error is not None
    assert result.error.code == "bot_detection"
