"""Static-vs-render routing heuristics."""

from __future__ import annotations

import re

JS_FRAMEWORK_SIGNALS = re.compile(
    r"__NEXT_DATA__|__NUXT__|ng-app|data-reactroot|data-v-app|"
    r' id="root"[^>]*>\s*</div>|id="app"[^>]*>\s*</div>|'
    r"window\.__INITIAL_STATE__|webpackJsonp|vite",
    re.I,
)

SPA_SHELL_PATTERNS = re.compile(
    r'<div[^>]+id=["\'](?:root|app|__next)["\'][^>]*>\s*</div>',
    re.I,
)

MIN_TEXT_LENGTH = 200


def looks_client_rendered(html: str) -> bool:
    """Return True when HTML looks like an empty JS app shell."""
    if SPA_SHELL_PATTERNS.search(html):
        text = _visible_text_length(html)
        if text < MIN_TEXT_LENGTH:
            return True
    if JS_FRAMEWORK_SIGNALS.search(html):
        text = _visible_text_length(html)
        if text < MIN_TEXT_LENGTH * 2:
            return True
    return False


def content_too_short(markdown: str, *, min_chars: int = 80) -> bool:
    return len(markdown.strip()) < min_chars


def should_use_render(
    html: str,
    markdown: str,
    *,
    render_mode: str,
) -> bool:
    if render_mode == "always":
        return True
    if render_mode == "never":
        return False
    if looks_client_rendered(html):
        return True
    if content_too_short(markdown):
        return True
    return False


def _visible_text_length(html: str) -> int:
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.I | re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return len(re.sub(r"\s+", " ", text).strip())
