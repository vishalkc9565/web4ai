"""Hybrid extractor: static-first with Playwright fallback for SPAs."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from web4ai.models import ExtractionError, ExtractionMeta, ExtractionResponse, ExtractOptions
from web4ai.pipeline.actions import extract_actions
from web4ai.pipeline.content_errors import detect_content_error
from web4ai.pipeline.fetch import FetchError, fetch_static
from web4ai.pipeline.markdown import count_tokens, html_to_markdown
from web4ai.pipeline.render import PlaywrightRenderer, get_renderer
from web4ai.pipeline.router import should_use_render


class HybridExtractor:
    """Attempt static fetch first; fall back to headless browser when needed."""

    def __init__(self, renderer: PlaywrightRenderer | None = None) -> None:
        self._renderer = renderer

    @property
    def renderer(self) -> PlaywrightRenderer:
        if self._renderer is None:
            self._renderer = get_renderer()
        return self._renderer

    async def extract(self, options: ExtractOptions) -> ExtractionResponse:
        url = str(options.url)
        timings: dict[str, float] = {}
        total_start = time.perf_counter()
        extraction_path = "static"
        strategy = "static"

        html = ""
        final_url = url
        status_code = 200
        fetch_error: ExtractionError | None = None

        if options.render != "always":
            try:
                fetch_result = await fetch_static(url, timeout_ms=options.timeout_ms)
                html = fetch_result.html
                final_url = fetch_result.final_url
                status_code = fetch_result.status_code
                timings["fetch"] = fetch_result.elapsed_ms
            except FetchError as exc:
                if options.render == "never":
                    return self._error_response(url, url, exc, timings, total_start)
                fetch_error = ExtractionError(
                    code=exc.code, message=exc.message, recoverable=exc.recoverable
                )

        markdown = ""
        truncated = False
        if html:
            extract_start = time.perf_counter()
            markdown, truncated = html_to_markdown(
                html,
                base_url=final_url,
                selector_hint=options.selector_hint,
                max_tokens=options.max_tokens,
            )
            timings["extract"] = (time.perf_counter() - extract_start) * 1000

        needs_render = options.render == "always" or (
            html and should_use_render(html, markdown, render_mode=options.render)
        )
        if not html and options.render != "never":
            needs_render = True

        render_error: ExtractionError | None = None
        if needs_render:
            try:
                render_result = await self.renderer.render(
                    url,
                    timeout_ms=options.timeout_ms,
                    wait_selector=options.js_wait_selector,
                )
                timings["render"] = render_result.elapsed_ms
                html = render_result.html
                final_url = render_result.final_url
                extraction_path = "render"
                strategy = "render"

                extract_start = time.perf_counter()
                markdown, truncated = html_to_markdown(
                    html,
                    base_url=final_url,
                    selector_hint=options.selector_hint,
                    max_tokens=options.max_tokens,
                )
                timings["extract"] = (time.perf_counter() - extract_start) * 1000
            except FetchError as exc:
                render_error = ExtractionError(
                    code=exc.code, message=exc.message, recoverable=exc.recoverable
                )
                if not markdown:
                    return self._error_response(
                        url, final_url, exc, timings, total_start, extraction_path
                    )

        actions = []
        if options.include_actions and options.actions != "none" and html:
            action_start = time.perf_counter()
            actions = extract_actions(html, base_url=final_url)
            timings["actions"] = (time.perf_counter() - action_start) * 1000

        token_count = count_tokens(markdown) if markdown else 0
        content_error = (
            detect_content_error(html, markdown, status_code=status_code) if html else None
        )
        error = render_error or content_error or fetch_error
        timings["total"] = (time.perf_counter() - total_start) * 1000

        return ExtractionResponse(
            markdown=markdown,
            actions=actions,
            meta=ExtractionMeta(
                url=url,
                final_url=final_url,
                fetched_at=datetime.now(UTC),
                extraction_path=extraction_path,  # type: ignore[arg-type]
                strategy=strategy,  # type: ignore[arg-type]
                truncated=truncated,
                tokens={"markdown": token_count},
                timings_ms=timings,
            ),
            error=error,
        )

    def _error_response(
        self,
        url: str,
        final_url: str,
        exc: FetchError,
        timings: dict[str, float],
        total_start: float,
        extraction_path: str = "static",
    ) -> ExtractionResponse:
        timings["total"] = (time.perf_counter() - total_start) * 1000
        return ExtractionResponse(
            meta=ExtractionMeta(
                url=url,
                final_url=final_url,
                fetched_at=datetime.now(UTC),
                extraction_path=extraction_path,  # type: ignore[arg-type]
                strategy="render" if extraction_path == "render" else "static",
                timings_ms=timings,
            ),
            error=ExtractionError(
                code=exc.code,
                message=exc.message,
                recoverable=exc.recoverable,
            ),
        )
