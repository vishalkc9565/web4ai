"""Static HTTP extraction pipeline."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from web4ai.models import ExtractionError, ExtractionMeta, ExtractionResponse, ExtractOptions
from web4ai.pipeline.actions import extract_actions
from web4ai.pipeline.content_errors import detect_content_error
from web4ai.pipeline.fetch import FetchError, fetch_static
from web4ai.pipeline.markdown import count_tokens, html_to_markdown


class StaticExtractor:
    async def extract(self, options: ExtractOptions) -> ExtractionResponse:
        url = str(options.url)
        timings: dict[str, float] = {}
        total_start = time.perf_counter()

        try:
            fetch_result = await fetch_static(url, timeout_ms=options.timeout_ms)
        except FetchError as exc:
            return ExtractionResponse(
                meta=ExtractionMeta(
                    url=url,
                    final_url=url,
                    fetched_at=datetime.now(UTC),
                    extraction_path="static",
                    strategy="static",
                    timings_ms={"total": (time.perf_counter() - total_start) * 1000},
                ),
                error=ExtractionError(
                    code=exc.code,
                    message=exc.message,
                    recoverable=exc.recoverable,
                ),
            )

        timings["fetch"] = fetch_result.elapsed_ms
        extract_start = time.perf_counter()

        markdown, truncated = html_to_markdown(
            fetch_result.html,
            base_url=fetch_result.final_url,
            selector_hint=options.selector_hint,
            max_tokens=options.max_tokens,
        )
        timings["extract"] = (time.perf_counter() - extract_start) * 1000

        actions = []
        if options.include_actions and options.actions != "none":
            action_start = time.perf_counter()
            actions = extract_actions(fetch_result.html, base_url=fetch_result.final_url)
            timings["actions"] = (time.perf_counter() - action_start) * 1000

        token_count = count_tokens(markdown)
        content_error = detect_content_error(
            fetch_result.html,
            markdown,
            status_code=fetch_result.status_code,
        )
        timings["total"] = (time.perf_counter() - total_start) * 1000

        return ExtractionResponse(
            markdown=markdown,
            actions=actions,
            meta=ExtractionMeta(
                url=url,
                final_url=fetch_result.final_url,
                fetched_at=datetime.now(UTC),
                extraction_path="static",
                strategy="static",
                truncated=truncated,
                tokens={"markdown": token_count},
                timings_ms=timings,
            ),
            error=content_error,
        )
