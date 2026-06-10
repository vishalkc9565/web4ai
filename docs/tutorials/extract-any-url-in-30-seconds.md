# Extract any URL in 30 seconds

**Audience:** AI/ML engineers and agent framework builders evaluating web4AI.

**Time:** ~30 seconds of reading + copy-paste.

**What you get:** Clean Markdown for reading *and* a structured action set for operating the page — not a raw HTML dump.

---

## 1. Install and run (one terminal)

```bash
git clone https://github.com/your-org/web4ai.git
cd web4ai
make install-dev
make run
```

`make install-dev` installs Python deps plus Playwright Chromium (needed for JS-heavy pages). The API listens on `http://localhost:8000`.

---

## 2. Extract a URL (one `curl`)

```bash
curl -s -X POST http://localhost:8000/v1/extract \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com"}' | jq .
```

`POST /extract` is an alias for the same handler if you prefer a shorter path.

---

## 3. Read the response

Every successful call returns JSON with three fields that matter for agents:

| Field | Purpose |
|-------|---------|
| `markdown` | Token-efficient page content — headings, links, tables, no nav/ads noise |
| `actions` | Callable surface: forms, buttons, and links an agent can invoke |
| `meta` | Provenance: final URL, fetch strategy (`static` vs `render`), timings, cache status |

### Example: static page (`example.com`)

```json
{
  "markdown": "This domain is for use in documentation examples without needing permission. Avoid use in operations.\n\nLearn more",
  "actions": [],
  "meta": {
    "url": "https://example.com/",
    "final_url": "https://example.com/",
    "extraction_path": "static",
    "strategy": "static",
    "tokens": { "markdown": 19 },
    "timings_ms": { "fetch": 1632, "extract": 147, "total": 1894 }
  },
  "error": null
}
```

Simple pages may return an empty `actions` array — there is nothing interactive to surface.

### Example: interactive page (`wikipedia.org`)

Pages with forms and buttons populate `actions` with typed items:

```json
{
  "markdown": "# Wikipedia\n\n**The Free Encyclopedia**\n\n...",
  "actions": [
    {
      "type": "form",
      "label": "Search Wikipedia",
      "target": "https://www.wikipedia.org/search-redirect.php",
      "method": "GET",
      "parameters": {
        "search": { "type": "search", "label": "Search Wikipedia" }
      },
      "description": "Submit a search query and load matching results."
    },
    {
      "type": "link",
      "label": "English",
      "target": "https://en.wikipedia.org/",
      "method": "GET",
      "parameters": {},
      "description": "Navigate to English Wikipedia."
    }
  ],
  "meta": {
    "url": "https://www.wikipedia.org/",
    "final_url": "https://www.wikipedia.org/",
    "extraction_path": "render",
    "strategy": "render",
    "timings_ms": { "render": 1700, "extract": 12, "total": 2100 }
  },
  "error": null
}
```

Each action has a `type` (`link`, `form`, or `button`), human-readable `label`, HTTP `target`/`method`, and typed `parameters` for form fields.

---

## 4. Optional request knobs

Pass these in the JSON body when you need more control:

```bash
curl -s -X POST http://localhost:8000/v1/extract \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://example.com",
    "render": "auto",
    "include_actions": true,
    "actions": "fast"
  }' | jq '{markdown: .markdown[0:120], action_count: (.actions | length), path: .meta.extraction_path}'
```

| Option | Default | Notes |
|--------|---------|-------|
| `render` | `"auto"` | `"never"` for static-only; `"always"` forces headless browser |
| `include_actions` | `true` | Set `false` for markdown-only (faster) |
| `actions` | `"fast"` | `"none"` skips action extraction; `"verified"` reserved for future tier |
| `use_cache` | `true` | Repeat requests return `X-Cache: HIT` |

Check response headers: `X-Cache: MISS` on first fetch, `HIT` on cached repeats.

---

## 5. Wire it into your agent

Minimal Python loop — fetch once, feed both markdown and tools to your agent:

```python
import httpx

resp = httpx.post(
    "http://localhost:8000/v1/extract",
    json={"url": "https://www.wikipedia.org"},
    timeout=60,
)
data = resp.json()

page_text = data["markdown"]
tools = [
    {"name": a["label"], "type": a["type"], "target": a.get("target"), "params": a["parameters"]}
    for a in data["actions"]
]
# Pass page_text + tools to your LLM / agent framework
```

Swap `httpx` for `fetch`, LangChain tool wrappers, or an MCP client — the API shape stays the same.

---

## 6. Go deeper

- **Interactive API reference:** [http://localhost:8000/docs](http://localhost:8000/docs) (Swagger UI) — try requests in-browser once the server is running.
- **Repo quick start:** [README.md](../../README.md) — `make ci`, integration tests, project layout.
- **Architecture context:** [design-spec.md](../../design-spec.md) — how static vs render paths and action tiers work.

---

## Pre-publish checklist (internal)

> **Tester sign-off required** before claiming extraction quality in public copy:
>
> - Accuracy of markdown on JS-heavy SPAs and paywalled pages
> - Action detection recall/precision on representative site corpus
> - `"verified"` action tier behavior (not yet exposed in v1 defaults)
>
> This draft uses live `curl` output from local dev; do not publish quality superlatives until [Tester](/WEB/agents/tester) validates against the golden corpus.

> **Hosted docs:** This tutorial lives in-repo at `docs/tutorials/`. A public docs site is **not** required for the draft. Escalate to [CTO](/WEB/agents/cto) if we need hosted docs + demo API before external publish.

---

## Your next step

```bash
curl -s -X POST http://localhost:8000/v1/extract \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://YOUR-TARGET-URL"}' | jq '{markdown: .markdown[0:200], actions: [.actions[] | {type, label}]}'
```

Replace `YOUR-TARGET-URL` with a site your agent needs to read and operate. That one call is the activation path.
