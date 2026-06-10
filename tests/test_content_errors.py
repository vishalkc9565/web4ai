"""Unit tests for content error heuristics."""

from web4ai.pipeline.content_errors import detect_content_error


def test_paywall_subscribe_to_read():
    err = detect_content_error(
        "<main><p>Subscribe to read this article</p></main>",
        "Subscribe to read this article",
    )
    assert err is not None
    assert err.code == "paywall"


def test_paywall_false_positive_paywalled_in_html_only():
    """Marketing copy like 'paywalled sites' in JSON-LD must not flag rich markdown."""
    html = (
        '<script type="application/ld+json">{"description":"scrapes paywalled sites"}</script>'
        "<main><h1>Firecrawl vs Tavily</h1><p>Full comparison content here.</p></main>"
    )
    markdown = (
        "# Firecrawl vs Tavily\n\n"
        "Choose Firecrawl when you need one API to search, crawl, and extract. "
        "Choose Tavily when you need real-time web search with AI-ranked results. "
        "This page compares features, pricing, and benchmarks in detail."
    )
    assert detect_content_error(html, markdown) is None


def test_paywall_standalone_word_in_thin_extraction():
    html = "<main><p>This content is behind a paywall. Subscribe for full access.</p></main>"
    err = detect_content_error(html, "This content is behind a paywall.")
    assert err is not None
    assert err.code == "paywall"


def test_bot_detection():
    html = "<main><p>Please verify you are human to continue browsing this site.</p></main>"
    err = detect_content_error(html, "Please verify you are human", status_code=200)
    assert err is not None
    assert err.code == "bot_detection"
