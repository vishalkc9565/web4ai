"""Unit tests for golden corpus validators."""

from golden_corpus import fixture_category, validate_fixture


def test_amazon_in_fixture_passes_on_captcha_interstitial():
    fixture = {
        "id": "amazon_in",
        "expectations": {
            "markdown": {"min_tokens": 10, "must_contain": ["JavaScript"]},
            "meta": {"strategy": "static"},
        },
    }
    response = {
        "markdown": (
            "# JavaScript is disabled\n\n"
            "In order to continue, we need to verify that you're not a robot."
        ),
        "actions": [],
        "meta": {"extraction_path": "static"},
        "error": None,
    }
    assert validate_fixture(fixture, response) == []
    assert fixture_category(fixture) == "listing"
