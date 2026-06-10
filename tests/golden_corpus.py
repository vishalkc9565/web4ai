"""Golden corpus expectation validators for live API runs."""

from __future__ import annotations

import re
from typing import Any

ACTION_NAME_PATTERNS: dict[str, re.Pattern[str]] = {
    "search_products": re.compile(r"search|query", re.I),
    "go_to_next_page": re.compile(r"\bnext\b|pagination|page[-/ ]?2", re.I),
    "search_quotes": re.compile(r"search|quote", re.I),
    "submit_contact_form": re.compile(r"submit|contact|custname", re.I),
    "accept_cookies": re.compile(r"accept.*cook", re.I),
    "reject_cookies": re.compile(r"reject.*cook", re.I),
    "manage_preferences": re.compile(r"manage.*pref|cookie.*pref", re.I),
}


def approx_tokens(text: str) -> int:
    return len(text.split())


def _action_text(action: dict[str, Any]) -> str:
    return " ".join(
        str(action.get(k, "")) for k in ("label", "description", "target", "method")
    )


def _find_action(actions: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    pattern = ACTION_NAME_PATTERNS.get(name)
    if pattern is None:
        pattern = re.compile(re.escape(name.replace("_", " ")), re.I)
    for action in actions:
        if pattern.search(_action_text(action)):
            return action
    return None


def validate_fixture(fixture: dict[str, Any], response: dict[str, Any]) -> list[str]:
    """Return a list of failing assertion messages (empty = pass)."""
    failures: list[str] = []
    expectations = fixture.get("expectations") or {}
    markdown = response.get("markdown") or ""
    actions = response.get("actions") or []
    meta = response.get("meta") or {}

    if expectations.get("expect_no_error") and response.get("error"):
        err = response["error"]
        failures.append(
            f"unexpected error: {err.get('code')!r} — {err.get('message', '')}"
        )

    md_exp = expectations.get("markdown") or {}
    if "min_tokens" in md_exp and approx_tokens(markdown) < md_exp["min_tokens"]:
        failures.append(
            f"markdown tokens {approx_tokens(markdown)} < min {md_exp['min_tokens']}"
        )
    for needle in md_exp.get("must_contain", []):
        if needle not in markdown:
            failures.append(f"markdown missing substring: {needle!r}")
    for needle in md_exp.get("must_not_contain", []):
        if needle in markdown:
            failures.append(f"markdown must not contain: {needle!r}")
    if md_exp.get("has_code_fence") and "```" not in markdown:
        failures.append("markdown missing code fence")

    meta_exp = expectations.get("meta") or {}
    strategy = meta_exp.get("strategy")
    if strategy:
        path = meta.get("extraction_path") or meta.get("strategy")
        if strategy == "static" and path not in ("static",):
            failures.append(f"extraction_path {path!r} != expected static")
        if strategy == "render" and path not in ("render",):
            failures.append(f"extraction_path {path!r} != expected render")

    act_exp = expectations.get("actions") or {}
    count = len(actions)
    if "min_count" in act_exp and count < act_exp["min_count"]:
        failures.append(f"actions count {count} < min {act_exp['min_count']}")
    if "max_count" in act_exp and count > act_exp["max_count"]:
        failures.append(f"actions count {count} > max {act_exp['max_count']}")

    for name in act_exp.get("must_include_names", []):
        if _find_action(actions, name) is None:
            failures.append(f"missing action: {name}")

    for name in act_exp.get("must_not_include_names", []):
        if _find_action(actions, name) is not None:
            failures.append(f"unexpected action present: {name}")

    if act_exp.get("must_include_effect") == "local":
        if not any("local" in (a.get("description") or "").lower() for a in actions):
            failures.append("no action with local/DOM effect found")

    return failures


def fixture_category(fixture: dict[str, Any]) -> str:
    fid = fixture.get("id", "")
    if fid.startswith("e2e_"):
        return "e2e"
    if "form" in fid or fixture.get("actions") == "verified":
        return "forms"
    if fixture.get("render") == "always":
        return "spa/render"
    if (
        "listing" in fid
        or "pagination" in fid
        or "books" in fid
        or "quotes" in fid
        or "amazon" in fid
    ):
        return "listing"
    return "read"
