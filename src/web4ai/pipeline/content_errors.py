"""Heuristic detection of partial extraction / content issues."""

from __future__ import annotations

import re

from web4ai.models import ExtractionError

PAYWALL_PATTERNS = re.compile(
    r"subscribe to (?:read|continue)|sign in to read|members only|"
    r"create an account to|premium content|\bpaywall\b",
    re.I,
)

# Rich markdown means extraction succeeded; ignore paywall mentions in raw HTML/JSON-LD.
_SUBSTANTIAL_MARKDOWN_CHARS = 200
BOT_PATTERNS = re.compile(
    r"access denied|verify you are human|captcha|cloudflare|"
    r"unusual traffic|blocked|forbidden",
    re.I,
)


def detect_content_error(
    html: str,
    markdown: str,
    *,
    status_code: int = 200,
) -> ExtractionError | None:
    if not markdown.strip() or len(markdown.strip()) < 20:
        if len(html.strip()) < 100:
            return ExtractionError(
                code="empty_page",
                message="Page appears empty or has no extractable content",
                recoverable=False,
            )

    stripped_md = markdown.strip()
    combined = f"{html}\n{markdown}"
    paywall_text = (
        stripped_md
        if len(stripped_md) >= _SUBSTANTIAL_MARKDOWN_CHARS
        else combined
    )
    if PAYWALL_PATTERNS.search(paywall_text):
        return ExtractionError(
            code="paywall",
            message="Content may be behind a paywall or login wall",
            recoverable=False,
        )
    if BOT_PATTERNS.search(combined) or status_code in (403, 503):
        return ExtractionError(
            code="bot_detection",
            message="Target may be blocking automated access",
            recoverable=True,
        )
    return None
