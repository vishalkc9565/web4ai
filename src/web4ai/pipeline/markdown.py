"""HTML to clean Markdown conversion with noise filtering."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import tiktoken
import trafilatura
from bs4 import BeautifulSoup, Comment
from markdownify import markdownify as md_convert

NOISE_SELECTORS = [
    "script",
    "style",
    "svg",
    "nav",
    "footer",
    "[role=banner]",
    "[role=navigation]",
    "[role=contentinfo]",
    "[class*=cookie]",
    "[class*=consent]",
    "[id*=cookie]",
    "[id*=consent]",
    "[class*=advert]",
    "[class*=ad-]",
    "aside.widget",
    ".pagetop",
    ".yclinks",
    "span.sitebit",
]

CHROME_TAGS = {"nav", "footer", "header", "aside"}

HN_HOSTS = {"news.ycombinator.com", "news.ycombinator.com."}


def _strip_noise(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup.find_all(string=lambda t: isinstance(t, Comment)):
        tag.extract()
    for selector in NOISE_SELECTORS:
        for el in soup.select(selector):
            el.decompose()
    return soup


def _preprocess_semantic_html(soup: BeautifulSoup, base_url: str | None) -> BeautifulSoup:
    """Promote table-layout and forum-style pages to heading structure."""
    host = urlparse(base_url or "").netloc.lower()
    if host in HN_HOSTS or soup.select_one("table.fatitem"):
        for titleline in soup.select("span.titleline"):
            title = titleline.get_text(" ", strip=True)
            if title:
                titleline.replace_with(soup.new_tag("h1", string=title))
        if soup.select(".comment-tree, table.comment-tree"):
            marker = soup.new_tag("h2")
            marker.string = "Comments"
            tree = soup.select_one(".comment-tree") or soup.select_one("table.comment-tree")
            if tree:
                tree.insert_before(marker)

    return soup


def _structured_markdown(soup: BeautifulSoup, base_url: str | None) -> str | None:
    """Build markdown for table-layout pages where generic converters lose structure."""
    host = urlparse(base_url or "").netloc.lower()
    if host in HN_HOSTS or soup.select_one("table.fatitem"):
        parts: list[str] = []
        title_el = soup.find("h1") or soup.select_one("span.titleline")
        if title_el:
            parts.append(f"# {title_el.get_text(' ', strip=True)}")
        subtext = soup.select_one("table.fatitem .subtext, tr.athing.submission + tr .subtext")
        if subtext:
            parts.append(subtext.get_text(" ", strip=True))
        if soup.select(".comment-tree, table.comment-tree"):
            parts.append("## Comments")
        for row in soup.select(".comment-tree .comtr, table.comment-tree .comtr"):
            head = row.select_one(".comhead")
            body = row.select_one(".commtext")
            if body:
                user = row.select_one("a.hnuser")
                user_label = user.get_text(strip=True) if user else ""
                if head and not user_label:
                    user_label = head.get_text(" ", strip=True).split(" on ")[0]
                prefix = f"**{user_label}** — " if user_label else ""
                parts.append(f"- {prefix}{body.get_text(' ', strip=True)}")
        if parts:
            return "\n\n".join(parts)
    return None


def _pick_main_content(soup: BeautifulSoup, selector_hint: str | None) -> BeautifulSoup:
    if selector_hint:
        match = soup.select_one(selector_hint)
        if match:
            return BeautifulSoup(str(match), "lxml")

    h1 = soup.find("h1")
    if h1:
        for ancestor in h1.parents:
            if getattr(ancestor, "name", None) in (None, "[document]", "html", "body"):
                break
            if len(ancestor.get_text(strip=True)) > 80:
                return BeautifulSoup(str(ancestor), "lxml")

    for selector in (
        "main",
        "[role=main]",
        "div.col-sm-8",
        "article",
        "#content",
        ".content",
        "section",
    ):
        match = soup.select_one(selector)
        if match and len(match.get_text(strip=True)) > 80:
            return BeautifulSoup(str(match), "lxml")

    body = soup.body or soup
    candidates = [
        el
        for el in body.find_all(["div", "section", "article"], recursive=True)
        if el.name not in CHROME_TAGS and len(el.get_text(strip=True)) > 120
    ]
    if candidates:
        best = max(
            candidates,
            key=lambda el: (
                len(el.find_all(["h1", "h2", "h3"])) * 500,
                len(el.get_text(strip=True)),
            ),
        )
        return BeautifulSoup(str(best), "lxml")
    return soup


def _source_has_headings(content: BeautifulSoup) -> bool:
    return bool(content.find(["h1", "h2", "h3", "h4"]))


def _markdown_has_headings(markdown: str) -> bool:
    return bool(re.search(r"^#{1,6}\s", markdown, re.M))


def _trafilatura_usable(content: BeautifulSoup, markdown: str) -> bool:
    if not markdown or len(markdown.strip()) < 40:
        return False
    if _source_has_headings(content) and not _markdown_has_headings(markdown):
        return False
    return True


def html_to_markdown(
    html: str,
    *,
    base_url: str | None = None,
    selector_hint: str | None = None,
    max_tokens: int | None = None,
) -> tuple[str, bool]:
    """Convert HTML to Markdown. Returns (markdown, truncated)."""
    soup = BeautifulSoup(html, "lxml")
    _strip_noise(soup)
    _preprocess_semantic_html(soup, base_url)
    structured = _structured_markdown(soup, base_url)
    if structured:
        markdown = structured
    else:
        content = _pick_main_content(soup, selector_hint)
        markdown = _html_fragment_to_markdown(content, base_url)

    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)

    truncated = False
    if max_tokens is not None:
        markdown, truncated = _truncate_markdown(markdown, max_tokens)

    return markdown, truncated


def _html_fragment_to_markdown(content: BeautifulSoup, base_url: str | None) -> str:
    extracted = trafilatura.extract(
        str(content),
        output_format="markdown",
        include_links=True,
        include_tables=True,
        url=base_url,
    )
    if extracted and _trafilatura_usable(content, extracted):
        return extracted.strip()
    return md_convert(
        str(content),
        heading_style="ATX",
        bullets="-",
        strip=["script", "style"],
    ).strip()


def count_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    try:
        enc = tiktoken.get_encoding(encoding_name)
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def _truncate_markdown(markdown: str, max_tokens: int) -> tuple[str, bool]:
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(markdown)
    if len(tokens) <= max_tokens:
        return markdown, False
    truncated = enc.decode(tokens[:max_tokens])
    return truncated.rstrip() + "\n\n…", True


def resolve_url(href: str, base_url: str) -> str:
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return href
    return urljoin(base_url, href)


def is_same_domain(url: str, base_url: str) -> bool:
    return urlparse(url).netloc == urlparse(base_url).netloc
