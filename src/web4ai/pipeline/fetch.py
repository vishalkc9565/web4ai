"""Static HTTP fetch with timeout and redirect handling."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (compatible; web4ai/0.1; +https://web4ai.dev) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class FetchResult:
    html: str
    final_url: str
    status_code: int
    elapsed_ms: float
    headers: dict[str, str]


class FetchError(Exception):
    def __init__(self, code: str, message: str, *, recoverable: bool = False):
        self.code = code
        self.message = message
        self.recoverable = recoverable
        super().__init__(message)


async def fetch_static(
    url: str,
    *,
    timeout_ms: int = 30000,
    max_redirects: int = 10,
) -> FetchResult:
    timeout = httpx.Timeout(timeout_ms / 1000)
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=max_redirects,
            timeout=timeout,
            headers={"User-Agent": DEFAULT_UA, "Accept": "text/html,application/xhtml+xml"},
        ) as client:
            response = await client.get(url)
    except httpx.TimeoutException as exc:
        raise FetchError(
            "timeout",
            f"Request timed out after {timeout_ms}ms",
            recoverable=True,
        ) from exc
    except httpx.TooManyRedirects as exc:
        raise FetchError("redirect_loop", "Too many redirects", recoverable=False) from exc
    except httpx.RequestError as exc:
        raise FetchError("network_error", str(exc), recoverable=True) from exc

    elapsed_ms = (time.perf_counter() - start) * 1000
    if response.status_code == 429:
        raise FetchError("http_429", "Rate limited by target", recoverable=True)
    if response.status_code >= 500:
        raise FetchError(
            f"http_{response.status_code}",
            f"Target returned HTTP {response.status_code}",
            recoverable=True,
        )
    if response.status_code >= 400:
        raise FetchError(
            f"http_{response.status_code}",
            f"Target returned HTTP {response.status_code}",
            recoverable=False,
        )

    return FetchResult(
        html=response.text,
        final_url=str(response.url),
        status_code=response.status_code,
        elapsed_ms=elapsed_ms,
        headers=dict(response.headers),
    )
