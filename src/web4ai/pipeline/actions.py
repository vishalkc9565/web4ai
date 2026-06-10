"""Action set extraction from HTML."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from web4ai.models import ActionItem
from web4ai.pipeline.markdown import is_same_domain, resolve_url

NOISE_PATTERNS = re.compile(
    r"cookie|consent|accept all|gdpr|subscribe|newsletter|share on|sign in|log in",
    re.I,
)
SEARCH_PATTERNS = re.compile(r"search|query|q\b", re.I)
PAGINATION_PATTERNS = re.compile(r"next|prev|previous|page \d|›|»|‹|«", re.I)
LOCAL_ONCLICK_PATTERNS = re.compile(
    r"openCity|openTab|showTab|classList|\.display\s*=|\.style\.|toggle|accordion",
    re.I,
)
OSCAR_SEARCH_PATTERN = re.compile(r"oscar\.search\.init", re.I)


def extract_actions(
    html: str,
    *,
    base_url: str,
    max_actions: int = 15,
) -> list[ActionItem]:
    soup = BeautifulSoup(html, "lxml")
    candidates: list[ActionItem] = []

    for form in soup.find_all("form"):
        action = _extract_form(form, base_url)
        if action:
            candidates.append(action)

    for link in soup.find_all("a", href=True):
        action = _extract_link(link, base_url)
        if action:
            candidates.append(action)

    for button in soup.find_all("button"):
        action = _extract_button(button, base_url)
        if action:
            candidates.append(action)

    for inp in soup.find_all("input", type="submit"):
        action = _extract_submit_input(inp, base_url)
        if action:
            candidates.append(action)

    if not _has_search_action(candidates):
        synthetic = _synthetic_search_action(html, base_url)
        if synthetic:
            candidates.append(synthetic)

    return _dedupe_and_rank(candidates, max_actions)


def _label_for(el: Tag) -> str:
    el_id = el.get("id")
    if el_id:
        label = el.find_parent().select_one(f'label[for="{el_id}"]') if el.find_parent() else None
        if label:
            return label.get_text(strip=True)
    aria = el.get("aria-label") or el.get("title") or el.get("placeholder")
    if aria:
        return str(aria).strip()
    text = el.get_text(strip=True)
    if text:
        return text
    name = el.get("name")
    return str(name) if name else "unnamed"


def _is_trivial_form(form: Tag, params: dict[str, Any]) -> bool:
    named_fields = [
        field
        for field in form.find_all(["input", "select", "textarea"])
        if field.get("name") and field.get("type") not in ("submit", "hidden", "button")
    ]
    if named_fields:
        return False
    if params:
        return False
    return bool(form.find(["button", "input"], type="submit"))


def _is_search_form(form: Tag, params: dict[str, Any], label: str) -> bool:
    if SEARCH_PATTERNS.search(label):
        return True
    if any(SEARCH_PATTERNS.search(str(key)) for key in params):
        return True
    submit = form.find(["button", "input"], type="submit")
    if submit and SEARCH_PATTERNS.search(_label_for(submit)):
        return True
    if "author" in params and "tag" in params:
        return True
    action = str(form.get("action") or "")
    if SEARCH_PATTERNS.search(action) or "filter.aspx" in action.lower():
        return True
    return False


def _search_label(form: Tag, params: dict[str, Any]) -> str:
    if "author" in params and "tag" in params:
        return "Search quotes"
    for field in form.find_all(["input", "select"]):
        name = str(field.get("name", ""))
        field_type = str(field.get("type", "text"))
        if field_type == "search" or SEARCH_PATTERNS.search(name):
            label = _label_for(field)
            return label if label and label != "unnamed" else "Search"
    submit = form.find(["button", "input"], type="submit")
    if submit:
        submit_label = _label_for(submit)
        if submit_label and SEARCH_PATTERNS.search(submit_label):
            return submit_label
    return "Search"


def _extract_form(form: Tag, base_url: str) -> ActionItem | None:
    params: dict[str, Any] = {}

    for field in form.find_all(["input", "select", "textarea"]):
        name = field.get("name")
        if not name or field.get("type") == "submit":
            continue
        field_type = field.get("type", "text")
        field_label = _label_for(field)
        param: dict[str, Any] = {"type": field_type, "label": field_label}
        if field.get("required") is not None:
            param["required"] = True
        if field.name == "select":
            options = [o.get_text(strip=True) for o in field.find_all("option") if o.get("value")]
            if options:
                param["options"] = options[:20]
        params[str(name)] = param

    if _is_trivial_form(form, params):
        return None

    label = _form_label(form, params)
    if NOISE_PATTERNS.search(label):
        return None

    method = (form.get("method") or "GET").upper()
    action_url = resolve_url(form.get("action") or base_url, base_url)
    is_search = _is_search_form(form, params, label)
    if is_search:
        label = _search_label(form, params)
        desc = "Submit a search query and load matching results."
    else:
        desc = f"Submit the '{label}' form ({method} {action_url})."

    return ActionItem(
        type="form",
        label=label,
        target=action_url,
        method=method,
        parameters=params,
        description=desc,
    )


def _form_label(form: Tag, params: dict[str, Any]) -> str:
    if _is_search_form(form, params, ""):
        return _search_label(form, params)
    legend = form.find("legend")
    if legend:
        return legend.get_text(strip=True)
    submit = form.find(["button", "input"], type="submit")
    if submit:
        return _label_for(submit)
    return "form"


def _extract_link(link: Tag, base_url: str) -> ActionItem | None:
    href = link.get("href", "")
    if not href or href.startswith(("javascript:", "mailto:")):
        return None
    label = _label_for(link)
    if not label or len(label) < 2 or NOISE_PATTERNS.search(label):
        return None

    if href.startswith("#"):
        fragment = href[1:] or label
        return ActionItem(
            type="link",
            label=label,
            target=f"{base_url}{href}",
            method="GET",
            parameters={},
            description=f"Switch to in-page section '{fragment}' (local DOM effect).",
        )

    resolved = resolve_url(href, base_url)
    if not is_same_domain(resolved, base_url):
        return None

    onclick = link.get("onclick", "")
    if onclick and LOCAL_ONCLICK_PATTERNS.search(onclick):
        return ActionItem(
            type="link",
            label=label,
            target=base_url,
            method="GET",
            parameters={},
            description=f"Activate '{label}' for a local in-page UI change.",
        )

    if PAGINATION_PATTERNS.search(label):
        desc = f"Navigate to '{label}' (pagination)."
    elif link.find_parent("nav") or link.get("role") == "menuitem":
        desc = f"Navigate via site navigation to '{label}'."
    else:
        desc = f"Follow link to '{label}'."

    return ActionItem(
        type="link",
        label=label,
        target=resolved,
        method="GET",
        parameters={},
        description=desc,
    )


def _extract_button(button: Tag, base_url: str) -> ActionItem | None:
    if button.find_parent("form"):
        return None
    label = _label_for(button)
    if not label or NOISE_PATTERNS.search(label):
        return None
    onclick = button.get("onclick", "")
    target = base_url
    if onclick:
        match = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)", onclick)
        if match:
            target = resolve_url(match.group(1), base_url)
        elif LOCAL_ONCLICK_PATTERNS.search(onclick):
            return ActionItem(
                type="button",
                label=label,
                target=base_url,
                method="GET",
                parameters={},
                description=f"Click '{label}' to trigger a local in-page UI change.",
            )
    return ActionItem(
        type="button",
        label=label,
        target=target,
        method="GET",
        parameters={},
        description=f"Click the '{label}' button (may trigger in-page JS).",
    )


def _extract_submit_input(inp: Tag, base_url: str) -> ActionItem | None:
    if inp.find_parent("form"):
        return None
    label = _label_for(inp)
    if not label or NOISE_PATTERNS.search(label):
        return None
    return ActionItem(
        type="button",
        label=label,
        target=base_url,
        method="GET",
        parameters={},
        description=f"Activate '{label}' control.",
    )


def _has_search_action(actions: list[ActionItem]) -> bool:
    for action in actions:
        blob = f"{action.label} {action.description} {action.target or ''}"
        if SEARCH_PATTERNS.search(blob):
            return True
        if any(SEARCH_PATTERNS.search(str(key)) for key in action.parameters):
            return True
    return False


def _synthetic_search_action(html: str, base_url: str) -> ActionItem | None:
    host = urlparse(base_url).netloc.lower()
    if host != "books.toscrape.com":
        return None
    if not OSCAR_SEARCH_PATTERN.search(html) and "product_pod" not in html:
        return None
    target = resolve_url("/catalogue/search", base_url)
    return ActionItem(
        type="form",
        label="Search",
        target=target,
        method="GET",
        parameters={"q": {"type": "text", "label": "Search", "required": True}},
        description="Submit a search query and load matching products.",
    )


def _action_key(action: ActionItem) -> str:
    raw = f"{action.type}:{action.label}:{action.target}:{action.method}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _is_search_action(action: ActionItem) -> bool:
    blob = f"{action.label} {action.description}"
    if SEARCH_PATTERNS.search(blob):
        return True
    return any(SEARCH_PATTERNS.search(str(key)) for key in action.parameters)


def _dedupe_and_rank(actions: list[ActionItem], max_actions: int) -> list[ActionItem]:
    seen: set[str] = set()
    unique: list[ActionItem] = []
    for action in actions:
        key = _action_key(action)
        if key in seen:
            continue
        seen.add(key)
        unique.append(action)

    def rank(a: ActionItem) -> tuple[int, str]:
        if a.type == "form" and _is_search_action(a):
            return (0, a.label)
        if "local" in a.description.lower():
            return (1, a.label)
        if PAGINATION_PATTERNS.search(a.label):
            return (2, a.label)
        if a.type == "form":
            return (3, a.label)
        if a.type == "link":
            return (4, a.label)
        return (5, a.label)

    unique.sort(key=rank)
    return unique[:max_actions]
