from pathlib import Path

from web4ai.pipeline.router import looks_client_rendered, should_use_render

FIXTURES = Path(__file__).parent / "fixtures"


def test_detects_spa_shell():
    html = (FIXTURES / "spa_shell.html").read_text()
    assert looks_client_rendered(html)


def test_static_article_not_spa():
    html = (FIXTURES / "simple_article.html").read_text()
    assert not looks_client_rendered(html)


def test_should_use_render_auto():
    spa = (FIXTURES / "spa_shell.html").read_text()
    assert should_use_render(spa, "", render_mode="auto")
