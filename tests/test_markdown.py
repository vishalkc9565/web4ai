from pathlib import Path

from web4ai.pipeline.markdown import count_tokens, html_to_markdown

FIXTURES = Path(__file__).parent / "fixtures"


def test_html_to_markdown_strips_nav_and_footer():
    html = (FIXTURES / "simple_article.html").read_text()
    md, truncated = html_to_markdown(html, base_url="https://example.com")
    assert "Web Scraping Guide" in md
    assert "Techniques" in md
    assert "cookie" not in md.lower()
    assert "Home" not in md  # nav stripped
    assert not truncated


def test_html_to_markdown_preserves_tables_and_code():
    html = (FIXTURES / "simple_article.html").read_text()
    md, _ = html_to_markdown(html)
    assert "Static" in md or "Fast" in md
    assert count_tokens(md) > 10


def test_truncate_markdown():
    html = "<main>" + "<p>word </p>" * 500 + "</main>"
    md, truncated = html_to_markdown(html, max_tokens=50)
    assert truncated
    assert count_tokens(md) <= 55


def test_hn_item_promotes_headings_and_comments():
    html = (FIXTURES / "hn_item.html").read_text()
    md, _ = html_to_markdown(html, base_url="https://news.ycombinator.com/item?id=1")
    assert "#" in md
    assert "Y Combinator" in md
    assert "Comments" in md
