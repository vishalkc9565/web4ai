"""Playwright headless browser rendering for JS-heavy pages."""

from __future__ import annotations

import time
from dataclasses import dataclass

from web4ai.pipeline.fetch import FetchError

BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}


@dataclass
class RenderResult:
    html: str
    final_url: str
    elapsed_ms: float


class PlaywrightRenderer:
    """Lazy-initialized Playwright browser for SPA extraction."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None

    async def start(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise FetchError(
                "js_rendering_unavailable",
                "Playwright not installed. Run: pip install web4ai[browser] "
                "&& playwright install chromium",
                recoverable=False,
            ) from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def render(
        self,
        url: str,
        *,
        timeout_ms: int = 30000,
        wait_selector: str | None = None,
    ) -> RenderResult:
        await self.start()
        assert self._browser is not None

        start = time.perf_counter()
        context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (compatible; web4ai/0.1) "
                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )
        page = await context.new_page()

        async def block_heavy(route, request):
            if request.resource_type in BLOCKED_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", block_heavy)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=min(timeout_ms, 8000))
                except Exception as exc:
                    raise FetchError(
                        "js_wait_timeout",
                        f"Selector '{wait_selector}' not found within timeout",
                        recoverable=True,
                    ) from exc
            else:
                try:
                    await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
                except Exception:
                    pass  # networkidle often times out on live sites; continue with partial DOM

            html = await page.content()
            final_url = page.url
        except FetchError:
            raise
        except Exception as exc:
            msg = str(exc)
            if "Timeout" in msg:
                raise FetchError(
                    "js_navigation_timeout",
                    f"Browser navigation timed out after {timeout_ms}ms",
                    recoverable=True,
                ) from exc
            raise FetchError(
                "js_rendering_failed",
                f"Browser rendering failed: {msg}",
                recoverable=True,
            ) from exc
        finally:
            await context.close()

        elapsed_ms = (time.perf_counter() - start) * 1000
        if len(html.strip()) < 50:
            raise FetchError(
                "js_empty_document",
                "Browser returned an empty document",
                recoverable=True,
            )

        return RenderResult(html=html, final_url=final_url, elapsed_ms=elapsed_ms)


_renderer: PlaywrightRenderer | None = None


def get_renderer() -> PlaywrightRenderer:
    global _renderer
    if _renderer is None:
        _renderer = PlaywrightRenderer()
    return _renderer
