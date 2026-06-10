from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from web4ai.extractor.hybrid import HybridExtractor
from web4ai.models import ExtractOptions
from web4ai.pipeline.fetch import FetchResult
from web4ai.pipeline.render import RenderResult

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_static_path_used_for_article():
    html = (FIXTURES / "simple_article.html").read_text()
    fetch = AsyncMock(
        return_value=FetchResult(
            html=html, final_url="https://example.com", status_code=200, elapsed_ms=5, headers={}
        )
    )
    renderer = MagicMock()
    renderer.render = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("web4ai.extractor.hybrid.fetch_static", fetch)
        result = await HybridExtractor(renderer=renderer).extract(
            ExtractOptions(url="https://example.com", render="auto")
        )

    assert result.meta.extraction_path == "static"
    renderer.render.assert_not_called()
    assert "Web Scraping Guide" in result.markdown


@pytest.mark.asyncio
async def test_render_fallback_for_spa_shell():
    shell = (FIXTURES / "spa_shell.html").read_text()
    rendered = (
        "<main><h1>Rendered Title</h1>"
        "<p>Client-side content loaded by JavaScript with enough text.</p></main>"
    )

    fetch = AsyncMock(
        return_value=FetchResult(
            html=shell, final_url="https://spa.example", status_code=200, elapsed_ms=5, headers={}
        )
    )
    renderer = MagicMock()
    renderer.render = AsyncMock(
        return_value=RenderResult(html=rendered, final_url="https://spa.example", elapsed_ms=1200)
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("web4ai.extractor.hybrid.fetch_static", fetch)
        result = await HybridExtractor(renderer=renderer).extract(
            ExtractOptions(url="https://spa.example", render="auto")
        )

    assert result.meta.extraction_path == "render"
    assert result.meta.strategy == "render"
    renderer.render.assert_called_once()
    assert "Rendered Title" in result.markdown


@pytest.mark.asyncio
async def test_render_always_skips_static_heuristic():
    renderer = MagicMock()
    renderer.render = AsyncMock(
        return_value=RenderResult(
            html="<main><h1>Only Render</h1><p>Content from browser path.</p></main>",
            final_url="https://example.com",
            elapsed_ms=500,
        )
    )
    fetch = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("web4ai.extractor.hybrid.fetch_static", fetch)
        result = await HybridExtractor(renderer=renderer).extract(
            ExtractOptions(url="https://example.com", render="always")
        )

    fetch.assert_not_called()
    assert result.meta.extraction_path == "render"
