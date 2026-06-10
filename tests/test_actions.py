from pathlib import Path

from web4ai.pipeline.actions import extract_actions

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_form_search_and_pagination():
    html = (FIXTURES / "books_listing.html").read_text()
    actions = extract_actions(html, base_url="https://books.toscrape.com/")
    labels = [a.label.lower() for a in actions]
    assert any("search" in label for label in labels)
    assert any("next" in label for label in labels)
    form_actions = [a for a in actions if a.type == "form"]
    assert form_actions[0].method == "GET"
    assert "q" in form_actions[0].parameters


def test_noise_links_suppressed():
    html = (FIXTURES / "simple_article.html").read_text()
    actions = extract_actions(html, base_url="https://example.com/")
    assert all("cookie" not in a.label.lower() for a in actions)


def test_action_has_description():
    html = (FIXTURES / "books_listing.html").read_text()
    actions = extract_actions(html, base_url="https://books.toscrape.com/")
    for action in actions:
        assert len(action.description) >= 10
        assert action.type in ("link", "form", "button")


def test_skips_trivial_basket_forms_and_synthesizes_search():
    html = """
    <html><body>
    <script>oscar.search.init();</script>
    <article class="product_pod"><h3><a>A Book</a></h3>
    <form><button type="submit">Add to basket</button></form></article>
  </body></html>
    """
    actions = extract_actions(html, base_url="https://books.toscrape.com/")
    labels = [a.label for a in actions]
    assert "Add to basket" not in labels
    assert any("search" in a.label.lower() for a in actions)


def test_quotes_search_form_detected():
    html = (FIXTURES / "quotes_search.html").read_text()
    actions = extract_actions(html, base_url="https://quotes.toscrape.com/search.aspx")
    search = next(a for a in actions if "search" in a.label.lower())
    assert search.method == "POST"
    assert "author" in search.parameters


def test_hash_links_are_local_tab_actions():
    html = (FIXTURES / "in_page_tab.html").read_text()
    actions = extract_actions(
        html, base_url="https://jqueryui.com/resources/demos/tabs/default.html"
    )
    assert any("local" in a.description.lower() for a in actions)
