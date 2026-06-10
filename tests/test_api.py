from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from web4ai.models import ExtractionError, ExtractionMeta, ExtractionResponse


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_extract_endpoint(client):
    mock_response = ExtractionResponse(
        markdown="# Hello",
        meta=ExtractionMeta(
            url="https://example.com",
            final_url="https://example.com",
            fetched_at=datetime.now(UTC),
            extraction_path="static",
        ),
    )
    with patch.object(
        client._transport.app.state.extractor,  # type: ignore[attr-defined]
        "extract_with_cache_check",
        AsyncMock(return_value=mock_response),
    ):
        r = await client.post("/extract", json={"url": "https://example.com"})
    assert r.status_code == 200
    data = r.json()
    assert data["markdown"] == "# Hello"
    assert r.headers.get("x-cache") == "MISS"


@pytest.mark.asyncio
async def test_extract_extraction_failure_returns_200_with_error_field(client):
    mock_response = ExtractionResponse(
        meta=ExtractionMeta(
            url="https://example.com",
            final_url="https://example.com",
            fetched_at=datetime.now(UTC),
        ),
        error=ExtractionError(code="timeout", message="timed out", recoverable=True),
    )
    with patch.object(
        client._transport.app.state.extractor,  # type: ignore[attr-defined]
        "extract_with_cache_check",
        AsyncMock(return_value=mock_response),
    ):
        r = await client.post("/v1/extract", json={"url": "https://example.com"})
    assert r.status_code == 200
    assert r.json()["error"]["code"] == "timeout"
