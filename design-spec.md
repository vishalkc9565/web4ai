# Agent-Ready Web API — Developer Implementation Guide

**Product:** An API that takes a URL and returns (1) clean, token-efficient Markdown of the page content and (2) a structured **action set** — a set of callable tools (forms, searches, buttons, pagination, navigation) that an AI agent can invoke to operate the site, not just read it.

**Audience:** Backend / infrastructure engineers building v1.

**Status:** Architecture + implementation spec. Code blocks are skeletons that show structure and the key libraries, not drop-in production code.

---

## 1. Scope and what makes this hard

The product does two things on every request:

1. **Read** — fetch a page (static HTML or a JS-rendered SPA), strip noise (nav, ads, scripts, cookie banners), and emit clean Markdown that is cheap in tokens and preserves semantic structure (headings, lists, tables, links).
2. **Act** — analyze the page's interactive surface and emit an **action set**: a machine-readable list of things an agent can *do* (submit this search form, go to the next page, add to cart, filter by category), expressed as typed, named tools with parameters and an execution contract.

The "read" half is a solved problem with good open-source building blocks and several commercial incumbents. **Your differentiation and most of the engineering risk lives in the "act" half** — turning an arbitrary page into a reliable, callable tool surface, and then actually executing those calls on behalf of an agent. Build the read pipeline fast with existing parts; spend your real effort on the action layer.

The three genuinely hard problems:

- **JS-heavy pages.** Most real sites render content and actions with JavaScript, so a plain HTTP fetch returns an empty shell. You need a headless browser for a large fraction of traffic, and that dominates latency and cost.
- **Action reliability.** Detecting interactive elements is easy; producing *stable, correctly-typed, semantically-labeled* actions that still work when invoked later is the hard part. Selectors break, forms have hidden CSRF tokens, flows span multiple pages.
- **Anti-bot and scale.** At volume you hit rate limits, WAFs, CAPTCHAs, and IP blocks, and you pay for browser compute. Architecture has to assume failure and degrade gracefully.

---

## 2. Language decision: Python-first, Rust for hot paths

### Recommendation

Build **v1 in Python**. Extract specific CPU-bound hot paths into **Rust** (called from Python via PyO3, or run as a separate Rust microservice) once v1 is validated and you need to cut per-request cost at scale.

### Why Python for v1

- **The bottleneck is I/O, not CPU.** A request is dominated by network round-trips and headless-browser rendering (often 0.5–5s). The language's raw compute speed is irrelevant to that 95% of the latency. Rust would make the fast part faster and leave the slow part unchanged.
- **Ecosystem.** Python has the most mature stack for every stage: `Playwright` for rendering, `trafilatura`/`readability-lxml` for content extraction, `selectolax`/`lxml`/`BeautifulSoup` for parsing, `markdownify`/`html2text` for conversion, plus first-class LLM SDKs and the agent frameworks (LangChain, LlamaIndex) you want integrations into. You ship weeks faster.
- **Async is good enough.** `FastAPI` + `asyncio` + `uvicorn`/`gunicorn` comfortably handles the concurrency you need when the real work is offloaded to a browser pool and workers.

### Where Rust genuinely earns its place (later)

- **HTML → Markdown conversion** on large documents — pure CPU, runs on every request, parallelizes well. A Rust converter (using `scraper`/`html5ever`) called via PyO3 can cut this stage 5–10x.
- **A high-throughput static-fetch + parse worker** for the subset of pages that don't need a browser — Rust with `reqwest` + `tokio` + `scraper` handles enormous concurrency on small memory.
- **Token counting / chunking** if you do it at volume.

### Where Rust is *not* worth it

- The headless-browser layer. Rust browser-automation crates (`chromiumoxide`, `thirtyfour`, `fantoccini`) work but are less mature and far less documented than Playwright, and the browser process is the cost — you save nothing by driving it from Rust.

### Net architecture implication

A pragmatic mature system is **a Python orchestration/API layer that calls out to (a) a Playwright browser pool and (b) optional Rust workers for CPU-bound stages.** Start all-Python; introduce the Rust workers behind the same internal interface so the swap is invisible to callers.

---

## 3. High-level architecture

```
                         ┌──────────────────────┐
   client / agent  ─────▶│   API Gateway        │  auth, rate limit, billing
   (REST or MCP)         │   (FastAPI)          │
                         └──────────┬───────────┘
                                    │ enqueue job
                                    ▼
                         ┌──────────────────────┐
                         │   Job Queue           │  Redis / RabbitMQ / SQS
                         └──────────┬───────────┘
                                    ▼
   ┌────────────────────────────────────────────────────────────┐
   │                    Extraction Workers                       │
   │                                                             │
   │   1. Fetch strategy router  (static? → fast path)           │
   │   2. Renderer  (Playwright browser pool, only if needed) ─┐ │
   │   3. Content extractor  (trafilatura)                     │ │ Rust
   │   4. Markdown converter  ─────────────────────────────────┘ │ hot
   │   5. Action extractor → TIER ROUTER:                        │ paths
   │        T-ignore  drop noise (no work)                       │ (opt)
   │        T0  static HTTP contracts (links/forms/OpenAPI/MCP)  │
   │        T1  lightweight server-side DOM (jsdom/happy-dom)    │
   │        T2  headless Chromium probe (residual only)          │
   │   6. Assembler → output schema                              │
   └───────────────────────┬─────────────────────────────────────┘
                            │
              ┌─────────────┼──────────────┐
              ▼             ▼              ▼
        ┌──────────┐  ┌──────────┐   ┌──────────────┐
        │  Cache   │  │ Proxy /  │   │  Action      │
        │ (Redis + │  │ anti-bot │   │  Execution   │  stateful browser
        │  blob)   │  │  pool    │   │  Service     │  sessions for replay
        └──────────┘  └──────────┘   └──────────────┘
```

Two request types share the pipeline:

- **`extract`** — synchronous-ish: URL in, `{markdown, actions}` out. Cacheable.
- **`act`** — invoke a previously-returned action (e.g. submit a form). Stateful, routed to the Action Execution Service which may hold a live browser session.

---

## 4. The extraction pipeline, stage by stage

### Stage 1 — Fetch strategy router

Decide cheaply whether a page needs a full browser. Browsers are ~50–100x more expensive than an HTTP GET, so route aggressively.

Heuristics, in order:
1. Try a plain HTTP GET first (with a real User-Agent, via `httpx`).
2. If the returned HTML has substantial text content and the interactive elements you care about are present in the static DOM → **static path** (no browser).
3. If the body is a near-empty shell (`<div id="root"></div>`), or content/actions are clearly client-rendered, or the GET is blocked → **render path** (browser).
4. Maintain a per-domain learned flag (cache "this domain needs JS") so you skip the probe next time.

```python
async def choose_strategy(url: str, domain_hints: DomainHints) -> Strategy:
    if domain_hints.requires_js(url):
        return Strategy.RENDER
    resp = await http_get(url)                  # httpx, real UA, redirects on
    if resp.status in BLOCKED_CODES:
        return Strategy.RENDER_WITH_PROXY
    if looks_client_rendered(resp.text):        # shell detection + text ratio
        domain_hints.mark_requires_js(url)
        return Strategy.RENDER
    return Strategy.STATIC                       # reuse resp.text downstream
```

### Stage 2 — Renderer (browser pool)

Use **Playwright** with a pool of persistent Chromium contexts. Key practices:

- Pool N browser instances; each serves many requests via fresh **contexts** (cheap) rather than fresh browser launches (expensive).
- Block images, fonts, media, and analytics by default (`route` interception) — you want the DOM, not pixels. This roughly halves render time and bandwidth.
- Wait on `networkidle` *or* a content signal with a hard timeout (e.g. 8s). Never wait unbounded.
- Capture: final HTML (`page.content()`), the accessibility tree (great signal for action labeling), and optionally a screenshot only if you later add vision-based extraction.
- Set realistic viewport, locale, timezone, UA. Reuse storage state for sites that need a session.

```python
async def render(url: str, pool: BrowserPool) -> RenderResult:
    ctx = await pool.acquire_context()
    try:
        page = await ctx.new_page()
        await page.route("**/*", _block_heavy_assets)
        await page.goto(url, wait_until="domcontentloaded", timeout=8000)
        await _settle(page)                      # networkidle OR selector OR timeout
        html = await page.content()
        ax_tree = await page.accessibility.snapshot()
        return RenderResult(html=html, ax_tree=ax_tree, final_url=page.url)
    finally:
        await pool.release_context(ctx)
```

> **Cost note.** This stage is your dominant cost and latency. Budget for it: cache hard, route static-first, autoscale workers on queue depth, and consider a managed browser provider early so you don't run a Chromium fleet on day one.

### Stage 3 — Content extraction (de-noise)

Goal: from full HTML, isolate the main content and drop chrome (nav, footer, ads, cookie/consent, related-posts, comment widgets).

- Primary tool: **`trafilatura`** — strong main-content extraction, handles articles, docs, and many app pages; can emit Markdown directly but you'll usually want finer control.
- Fallback: **`readability-lxml`** for article-style pages.
- For app/dashboard-style pages where "main content" is fuzzy, fall back to a structural cleaner: remove `script`/`style`/`svg`/`nav`/`footer`/`[role=banner]`/known ad/consent selectors, then keep the largest meaningful content block.

Keep both the **cleaned content DOM** (for Markdown) and the **full DOM** (the action extractor needs the nav/forms you just stripped from the content view).

### Stage 4 — HTML → token-efficient Markdown

Convert the cleaned content DOM to Markdown optimized for token cost:

- Preserve: headings, lists, tables, code blocks, links (with text), emphasis where semantically meaningful.
- Collapse: repeated whitespace, empty elements, redundant nesting.
- Tables: emit GitHub-flavored Markdown tables; if a table is huge, summarize structure and truncate with a marker.
- Links: keep anchor text; optionally strip or shorten tracking query params. Decide policy on inlining vs. reference-style links (reference style is more token-efficient for link-heavy pages).
- Always return token counts (per your billing tokenizer and a generic one like `tiktoken`) in the response metadata.

Python: `markdownify` or `html2text` to start. **This is the first stage to port to Rust** (`html5ever` + custom serializer) when volume justifies it — it's pure CPU and runs on every request.

```python
def to_markdown(content_dom: Node, opts: MarkdownOpts) -> MarkdownResult:
    md = converter.convert(content_dom, opts)     # markdownify/html2text now;
    md = postprocess(md, opts)                     # rust converter later
    return MarkdownResult(
        markdown=md,
        token_estimate=count_tokens(md),
    )
```

### Stage 5 — Action set extraction (the differentiator)

This is where the product wins or loses. The job: turn the **full DOM + accessibility tree** into a list of typed, named, documented actions an agent can call.

> The section below is the v1 overview. **The production-grade design — the method I recommend you actually build — is in [Appendix A: The Action-Set Extraction Engine](#appendix-a--the-action-set-extraction-engine-advanced).** It replaces "detect and guess" with "detect, *verify by probing*, and self-heal," which is what makes it reliable enough to trust.

#### 5.0 — Tiered extraction model (static-first, probe-the-residual)

The headline efficiency principle: **most actions never need a browser, and many candidates never need any work at all.** Every candidate is routed to the cheapest tier that can resolve it, and a real browser is the exception, not the default. There are four tiers, including an explicit *ignored* tier so noise costs nothing:

| Tier | What it handles | Cost | Verification | Confidence |
|---|---|---|---|---|
| **T-ignore** | Noise: social-share, cookie/consent, analytics, decorative, duplicate nav, ad widgets, and (by policy) destructive controls you won't auto-probe | ~0 (dropped) | n/a — suppressed | n/a |
| **T0 — static HTTP** | Links (`GET href`), standard `<form>`s, and discovered API descriptions (OpenAPI/Swagger, GraphQL introspection, WebMCP/`.well-known` declared tools) | Cheapest; no JS engine | by server-side request *replay* (no browser) | high |
| **T1 — light DOM** | JS-handled controls whose effect is observable in a server-side DOM emulator (jsdom / happy-dom / linkedom) — fire synthetic event, watch in-process `fetch`/XHR + mutations | Low; Node worker, no Chromium | in-process observation | medium–high |
| **T2 — browser probe** | The opaque residual: complex SPA widgets, canvas, anything T1 can't faithfully execute | Highest; pooled headless Chromium | full CDP probe (Appendix A.5) | high when verified |

The router classifies each candidate **top-down**: drop it (T-ignore) → can it be expressed as a static HTTP contract (T0) → can a lightweight DOM resolve it (T1) → only then a real browser (T2). Tiers also **fall back downward at runtime**: if T1's emulator throws on a gnarly page, that candidate escalates to T2; if a T0 discovered-API replay 4xx's, it escalates to a browser check. The net effect is that a large share of requests complete with **zero browser launches**, and even T2 work is cached per page-template so you pay it once (Appendix A.9).

```python
def route_tier(c: InteractionCandidate, policy: Policy) -> Tier:
    # T-ignore: never spend a cycle on noise or no-auto-probe controls
    if policy.is_noise(c) or is_duplicate(c) or policy.is_destructive(c):
        return Tier.IGNORE
    # T0: already a static HTTP contract — no execution needed to know the request
    if c.source == "webmcp" or c.discovered_api is not None:
        return Tier.T0_HTTP
    if c.role == "link" and c.raw_attrs.get("href"):
        return Tier.T0_HTTP
    if c.form_model is not None and c.form_model.is_standard_submit():
        return Tier.T0_HTTP
    # T1: JS-handled but plausibly resolvable in a lightweight DOM
    if c.role in LIGHT_DOM_RESOLVABLE_ROLES and not c.needs_full_browser_hint:
        return Tier.T1_LIGHT_DOM
    # T2: opaque residual — real browser
    return Tier.T2_BROWSER

# request-level override: `actions: "fast"` caps the ladder so callers can trade
# certainty for speed (skip T2, return T0 verified + T1/heuristic inferred).
def cap_tier(tier: Tier, mode: Literal["fast", "verified"]) -> Tier:
    if mode == "fast" and tier == Tier.T2_BROWSER:
        return Tier.T1_LIGHT_DOM        # degrade to inferred, lower confidence
    return tier
```

The **ignored tier is doing real work**, not just discarding: it is what keeps the action set small and high-signal (agents drown in 200 link-soup "actions") and what guarantees you never auto-execute something destructive. Everything T-ignore drops is still *logged* with a reason, so you can audit suppression and let callers opt into a `include_suppressed` debug view.

Detection mechanics for the candidates that survive routing:


- **Forms** — each `<form>` with its fields. For each field capture: name, `type`, label (from `<label>`, `aria-label`, placeholder, or nearby text), required, options (for `select`/radio), and the form's `action`/`method`. Detect and flag hidden fields (CSRF tokens, etc.) so execution can preserve them.
- **Search** — a form or input that is semantically search (heuristics: `type=search`, `role=search`, name/placeholder containing "search", magnifier icon). Promote these — they're high-value actions.
- **Navigation / pagination** — "next/prev", numbered pagers, "load more", infinite scroll triggers, breadcrumb and primary nav links.
- **Buttons / interactive controls** — buttons with clear intent (add to cart, filter, sort, toggle), tabs, accordions.
- **Filters / facets** — checkbox/select groups that refine a listing.

Use the **accessibility tree** heavily here: it gives you role + accessible name for free and is far more stable than scraping visual layout.

**5b. Classification + labeling (heuristics, optionally LLM-assisted).** Convert raw candidates into clean, named tools:

- Deterministic first: map element + label → a tool name (`search_products`, `go_to_next_page`, `filter_by_category`, `submit_contact_form`) and parameter schema (JSON Schema derived from field types).
- Optional LLM pass for the ambiguous remainder: send a compact description of the candidate elements (NOT the raw HTML — too many tokens) and ask the model to (a) name and describe each action in agent-friendly language, (b) drop noise (cookie buttons, social share), (c) infer parameter descriptions. Cache results per (domain, page-template) so you don't pay the LLM on every request to the same kind of page.
- This LLM step is itself a cost/latency lever — make it optional via a request flag (`actions: "fast" | "rich"`).

**5c. Build the execution contract.** Each action needs enough to *replay* it later:

- **HTTP-expressible actions** (a form that GETs/POSTs to an endpoint): record method, URL, field mapping, and required hidden fields. These can be executed with a plain HTTP client — cheap and reliable.
- **DOM-expressible actions** (JS-driven, no clean endpoint): record a **stable selector strategy** (prefer accessibility name + role, then `data-*`/`id`, then a structural fallback) plus the interaction type (click, fill+submit). These require a browser session to execute.
- Assign each action a stable `action_id` and mark `execution: "http" | "browser"`.

```python
def extract_actions(full_dom, ax_tree, opts) -> list[Action]:
    candidates = detect_candidates(full_dom, ax_tree)   # forms, search, nav, buttons
    actions = []
    for c in candidates:
        kind = classify(c)                               # heuristic first
        if kind is AMBIGUOUS and opts.rich:
            kind = llm_classify(c)                       # optional, cached
        if kind is NOISE:
            continue
        actions.append(Action(
            id=stable_id(c),
            name=tool_name(c, kind),
            description=describe(c, kind),
            parameters=json_schema_for(c),               # JSON Schema
            execution=execution_contract(c),             # http | browser
        ))
    return dedupe_and_rank(actions)                      # most useful first
```

**5d. Output as MCP-compatible tools.** Shape each action so it maps directly onto an MCP tool definition / JSON-Schema function spec. This is what lets an agent framework call your actions natively — and it's your distribution wedge (see §6).

### Stage 6 — Assembler

Combine into the response schema (§7), attach metadata (timings, token counts, strategy used, cache status), and persist a job record for the `act` endpoint to reference.

---

## 5. Action execution service

When an agent calls `POST /act` with an `action_id` + parameters, you execute it:

- **HTTP actions:** build and send the request server-side (preserving hidden fields, cookies, session), then run the *result* page back through the extraction pipeline → return new `{markdown, actions}`. Cheap, stateless-ish.
- **Browser actions:** route to a **stateful browser session**. Keep a session keyed by an opaque `session_id` so multi-step flows (search → open result → add to cart → checkout) reuse one browser context and its cookies. Expire sessions on a TTL; cap concurrent sessions per customer.

Design the loop so an agent can chain: every `act` response returns the new page's Markdown **and** its new action set, so the agent always has both "what do I see" and "what can I do next." This read+act loop is the core product experience.

```python
@app.post("/act")
async def act(req: ActRequest) -> ExtractResponse:
    action = await store.get_action(req.action_id)
    if action.execution == "http":
        page = await execute_http_action(action, req.params, req.session)
    else:
        session = await sessions.get_or_create(req.session_id)
        page = await execute_browser_action(session, action, req.params)
    return await run_pipeline(page)          # returns new {markdown, actions}
```

---

## 6. API surface

### REST

- `POST /v1/extract` — `{ url, render?: auto|always|never, actions?: none|fast|rich, formats?: [markdown, actions] }` → `ExtractResponse`
- `POST /v1/act` — `{ action_id, params, session_id? }` → `ExtractResponse`
- `POST /v1/crawl` — multi-page (queue a crawl, webhook/poll for results) — phase 2
- `GET  /v1/jobs/{id}` — async job status

### MCP server (ship this — it's the distribution channel)

Expose the same capability as an MCP server so any MCP-compatible agent/client can use it without custom integration. Minimum tools:

- `read_page(url)` → markdown + actions
- `do_action(action_id, params, session_id?)` → markdown + actions
- (optional) `search_site(url, query)` convenience wrapper

Also publish thin adapters/integration guides for LangChain, LlamaIndex, CrewAI, and the OpenAI Agents SDK that wrap the REST API. These are small but high-leverage — they put you where agents are built.

---

## 7. Response schema

```jsonc
{
  "url": "https://example.com/products",
  "final_url": "https://example.com/products",
  "fetched_at": "2026-06-10T12:00:00Z",
  "markdown": "# Products\n\n...",
  "actions": [
    {
      "id": "act_7f3a...",
      "name": "search_products",
      "description": "Search the product catalog by keyword.",
      "parameters": {
        "type": "object",
        "properties": { "query": { "type": "string", "description": "Search keywords" } },
        "required": ["query"]
      },
      "execution": "http"
    },
    {
      "id": "act_9b2c...",
      "name": "go_to_next_page",
      "description": "Load the next page of results.",
      "parameters": { "type": "object", "properties": {} },
      "execution": "browser"
    }
  ],
  "meta": {
    "strategy": "render",
    "cache": "miss",
    "tokens": { "markdown": 1840 },
    "timings_ms": { "fetch": 120, "render": 1430, "extract": 90, "actions": 210 }
  }
}
```

---

## 8. Cross-cutting concerns

### Caching (your margin lives here)
- Cache `extract` results by normalized URL + options, with a content-hash and per-domain TTL. A high cache-hit rate is the difference between a viable and unviable cost structure.
- Cache the "domain needs JS" flag and learned per-domain extraction tweaks.
- Cache LLM action-classification by (domain, page-template signature).

### Concurrency & scale
- Stateless API layer behind a load balancer; horizontal scale.
- Workers pull from the queue; autoscale on **queue depth** and browser-pool saturation, not CPU.
- Hard per-request timeouts and circuit breakers per domain.
- Separate pools/quotas for `extract` (bursty, cacheable) vs `act` (stateful, sticky sessions).

### Anti-bot, proxies, compliance
- Rotating residential/datacenter proxy pool, retry with backoff on blocks; escalate static → render → render+proxy.
- Respect `robots.txt` and per-customer allow/deny policies; rate-limit per target domain to be a good citizen.
- **Legal:** scraping public data is broadly permissible in many jurisdictions but varies; you must respect robots.txt, terms of service, and copyright, and watch GDPR/CCPA exposure. Put policy controls in the product (domain allow-lists, PII filters) and get counsel before enabling logged-in/transactional actions for customers. Treat this as a product requirement, not an afterthought.

### Observability & billing
- Per-stage timing + cost attribution per request (browser-seconds, LLM tokens, proxy bandwidth) — you need this for usage-based pricing.
- Structured logs, traces (OpenTelemetry), and a per-customer usage meter from day one.

### Security
- Treat every fetched page as hostile input. Sandbox browsers (no host network access beyond the proxy egress), strip/ignore page-supplied instructions, cap response sizes, and never let a page's content trigger privileged actions. For the `act` endpoint, require the agent's caller to authorize the action — don't auto-execute destructive actions (purchase, delete) without explicit opt-in flags.

---

## 9. Suggested tech stack (v1, all-Python)

| Concern | Choice |
|---|---|
| API framework | FastAPI + uvicorn/gunicorn |
| HTTP fetch | httpx (async) |
| Headless browser | Playwright (Chromium), pooled contexts |
| Content extraction | trafilatura (+ readability-lxml fallback) |
| HTML parsing | selectolax / lxml |
| HTML→Markdown | markdownify or html2text (→ Rust later) |
| Tokenization | tiktoken (+ your billing tokenizer) |
| Queue | Redis (RQ/Celery) or SQS to start |
| Cache | Redis + object storage (S3/R2) for large payloads |
| Datastore | Postgres (jobs, actions, usage) |
| LLM (optional action labeling) | any provider SDK, behind an interface, cached |
| MCP | MCP server SDK exposing read/act tools |
| Deploy | containers + autoscaling (k8s or a managed platform); consider a managed browser provider initially |

---

## 10. Project structure (Python)

```
agent_ready_api/
├── api/                 # FastAPI app, routes, auth, rate limiting
│   ├── routes/extract.py
│   ├── routes/act.py
│   └── schemas.py
├── pipeline/
│   ├── router.py        # static-vs-render decision
│   ├── fetch.py         # httpx static fetch
│   ├── render.py        # Playwright pool + render
│   ├── extract.py       # trafilatura content extraction
│   ├── markdown.py      # HTML->Markdown (swap to rust binding later)
│   ├── actions/
│   │   ├── detect.py    # deterministic candidate detection
│   │   ├── classify.py  # heuristics + optional LLM
│   │   └── contract.py  # build execution contracts + stable ids
│   └── assemble.py
├── execution/
│   ├── http_action.py
│   ├── browser_action.py
│   └── sessions.py      # stateful browser session manager
├── mcp/                 # MCP server exposing read/act tools
├── infra/
│   ├── cache.py
│   ├── queue.py
│   ├── proxies.py
│   └── browser_pool.py
├── billing/usage.py
└── tests/
```

When you add Rust: a `rust_markdown/` crate built with PyO3/maturin exposing `to_markdown(html, opts) -> str`, imported in `pipeline/markdown.py` behind the same function signature so nothing else changes.

---

## 11. Build roadmap

**Milestone 0 — Read path (1–2 weeks).** `POST /extract` for static + rendered pages → clean Markdown with token counts. Caching + basic auth. This alone matches the table-stakes incumbents.

**Milestone 1 — Action set, deterministic (2–3 weeks).** Candidate detection + heuristic classification + JSON-Schema output. Forms, search, pagination, primary nav. Ship the MCP server exposing `read_page`. **This is the demo that differentiates you** — an agent reading *and* listing what it can do.

**Milestone 2 — Execution loop (2–3 weeks).** `POST /act` for HTTP actions, then browser actions with stateful sessions. Now an agent can chain read→act→read. Record a video of an agent operating a real site end-to-end — this is your launch asset.

**Milestone 3 — Rich actions + scale hardening (ongoing).** Optional LLM labeling (cached), proxy/anti-bot escalation, autoscaling on queue depth, per-customer usage metering, crawl endpoint.

**Milestone 4 — Rust hot paths (when volume justifies).** Port Markdown conversion to a PyO3 crate; add a Rust static-fetch+parse worker for the no-browser subset. Measure first; only port stages that show up in cost/latency profiling.

---

## 12. Performance budget (target, per request)

| Path | Target p50 | Dominated by |
|---|---|---|
| Static + cache hit | < 50 ms | serialization |
| Static, no browser | 200–600 ms | network fetch |
| Rendered | 1.5–4 s | browser render |
| Rich actions (LLM) | +0.3–1.5 s | model call (cache to avoid) |

Optimization order: **cache hit rate → static-first routing → asset blocking in browser → Rust markdown → everything else.** Do not start with the Rust rewrite; start with caching and routing, which dwarf it.

---

## 13. Testing

- **Golden-page corpus:** a fixed set of saved HTML pages (article, docs, e-commerce listing, search form, SPA dashboard, paginated list) with expected Markdown and expected action sets. Run on every change to catch extraction regressions.
- **Action-replay tests:** for each detected action, assert it executes and returns a plausible next page (against recorded/mocked targets).
- **Selector-stability tests:** re-render the same page and confirm `action_id`s and selectors stay stable.
- **Load tests:** queue-depth autoscaling, browser-pool saturation, timeout behavior under blocked domains.
- **Adversarial input:** malformed HTML, infinite redirects, huge pages, prompt-injection content in the page body (confirm it never alters behavior).

---

## 14. Summary of the key decisions

1. **Python for v1**, Rust for the Markdown converter and a static-fetch worker later — because rendering I/O, not CPU, is the bottleneck.
2. **Route static-first**; only use the browser when you must — it's your dominant cost.
3. **The action set is the product, and the recommended method is verify-by-probing (Appendix A).** Build the read path from existing parts fast; for actions, don't guess from markup — harvest candidates from the accessibility tree + form model, then *probe each in a sandboxed browser while watching the network* to verify its effect and lift it to a stable HTTP call, attach self-healing locators, cache per page-template, and ship every action with a confidence + verification label. Reliability comes from empirical verification and graceful degradation, not a single clever detector.
4. **Ship an MCP server + framework adapters** — that's distribution, not just an API.
5. **Cache aggressively and meter everything** — it's the difference between viable and unviable margins.
6. **Treat compliance and page-as-hostile-input as product requirements**, especially before enabling transactional actions.

---

# Appendix A — The Action-Set Extraction Engine (advanced)

This appendix specifies the production-grade method for turning an arbitrary web page into a reliable, callable action set. It is the most important and hardest part of the product, so it gets the most rigor.

## A.0 An honest framing of "works for sure"

No method extracts actions from the *arbitrary* web with 100% success — sites are adversarial, dynamic, and infinitely varied, and anyone promising certainty is selling you something. What you can engineer is **high, *measured*, self-healing reliability**: actions that are verified to work before you hand them out, carry a confidence score, and re-ground themselves when the page shifts. For an agent, a *known* 0.95-confidence action plus a clean failure signal is far more valuable than an unverified action that silently breaks. So the design goal is not "never fail" — it's **"never fail silently, verify before promising, and recover automatically when possible."**

The single idea that makes this work: **stop guessing what an element does from its markup; instead trigger it in a sandbox and observe what actually happens.** Verification by probing is what converts a brittle heuristic into a dependable contract.

## A.1 Recommended method in one paragraph

Harvest interaction candidates from the **highest-fidelity signals first** — any declared agent interface (WebMCP / `.well-known` manifests), then the **accessibility (AX) tree** via the Chrome DevTools Protocol, then the **HTML form model**, falling back to a **vision-language re-grounding pass** only for the residual. Normalize all of them into one `InteractionCandidate` type. Then **lift each candidate into a verified, executable contract** by probing it in an isolated browser context while observing DOM mutations, navigations, and — crucially — **network traffic**, which lets you promote a brittle DOM click into a stable direct **HTTP API call** whenever an underlying endpoint exists. Synthesize a **JSON Schema** for parameters from form constraints and observed request payloads. Attach a **ranked, self-healing locator bundle** so execution survives DOM drift. Use an **LLM only for naming, describing, deduping, and ranking** — never for detection — and cache that semantic layer per **page-template fingerprint**. Emit every action with a **confidence score and verification status**.

Fidelity ladder (always prefer higher):

1. **Declared interface** — site exposes WebMCP tools / an agent manifest → use directly. Highest fidelity, future-proof. (Most sites won't have this yet; that gap is your business.)
2. **Discovered API** — probing reveals the XHR/fetch endpoint behind an action → express as direct HTTP. Stable, fast, cheap to execute.
3. **AX-grounded DOM action** — role + accessible name from the AX tree, executed via a browser session. Robust to styling changes.
4. **Structural DOM action** — fallback selector path. Brittle; lowest confidence; flagged as such.

## A.2 Pipeline overview

```
full DOM + AX tree + CDP session
        │
        ▼
[1] Signal harvesting ─── WebMCP/manifest │ AX tree │ form model │ (VLM residual)
        │
        ▼
[2] Normalize → InteractionCandidate[]   (dedup overlapping signals)
        │
        ▼
[3] Interaction probing (sandboxed)  ── observe DOM Δ + navigation + network
        │                                 → EffectClass + observed request(s)
        ▼
[4] API lifting           ── promote click → HTTP action when endpoint found
        │
        ▼
[5] Schema synthesis      ── JSON Schema from form constraints + observed params
        │
        ▼
[6] Locator bundle        ── ranked self-healing strategies + semantic fingerprint
        │
        ▼
[7] Semantic enrichment   ── LLM: name, describe, dedup, rank   (cached per template)
        │
        ▼
[8] Confidence + verify   ── score; mark verified|inferred|unverified
        │
        ▼
   Action[]  (+ template fingerprint cached)
```

Stages 3–4 are the part nobody else does well and the reason this method is reliable. Everything else is supporting infrastructure.

## A.3 The unified candidate model

Everything from every signal collapses into one structure before probing, so downstream stages are signal-agnostic.

```python
@dataclass
class InteractionCandidate:
    source: Literal["webmcp", "openapi", "graphql", "ax", "form", "vlm"]
    role: str                       # button | textbox | combobox | link | search | form ...
    accessible_name: str            # computed name (AX) or label (form)
    node_ref: NodeRef               # CDP backendNodeId + a resolved locator bundle
    form_model: FormModel | None    # fields, method, action, enctype, hidden fields
    discovered_api: ApiDescriptor | None  # set when a static API description covers this
    raw_attrs: dict                 # id, data-*, name, type, href, aria-*
    region: Literal["main","nav","header","footer","aside","dialog"]  # from landmarks
    bbox: Rect | None               # for VLM grounding / overlap dedup
    needs_full_browser_hint: bool = False   # set by static JS analysis / role heuristics
    tier: Tier | None = None        # assigned by route_tier()
```


`NodeRef` carries the CDP `backendNodeId` (stable within a page lifetime) so probing and locator generation operate on the exact node the AX tree referenced — no re-querying by selector, which is where drift creeps in.

## A.4 Stage 1 — Signal harvesting

### A.4.1 Declared interfaces first
Check for a machine interface before doing any inference:
- Fetch `/.well-known/` for agent/MCP manifests; check for WebMCP tool declarations exposed by the page (declarative `tool`-typed elements / the WebMCP imperative registry).
- If present, map declared tools straight into your `Action` schema with `source="webmcp"`, `confidence=0.99`. You're done for those — no probing needed. This path will grow over time; design for it now so you ride the standard instead of fighting it.

### A.4.2 Accessibility tree (primary signal for the rest)
Pull the full AX tree over CDP, not the trimmed Playwright snapshot — you want backend node ids and the full property set:

```python
async def harvest_ax(page) -> list[AXNode]:
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Accessibility.enable")
    tree = await cdp.send("Accessibility.getFullAXTree")
    return [n for n in tree["nodes"] if n.get("role", {}).get("value") in ACTIONABLE_ROLES]

ACTIONABLE_ROLES = {
    "button", "link", "textbox", "searchbox", "combobox", "checkbox",
    "radio", "menuitem", "tab", "switch", "slider", "listbox", "option",
}
```

Why AX first: the **accessible name** is computed by the browser the same way assistive tech computes it (label association, `aria-label`, `aria-labelledby`, text content, `title`, in spec order). It is dramatically more stable and more semantically meaningful than scraping visible text or class names, and it directly gives you `getByRole(role, name=...)` locators, which survive restyling and most refactors.

### A.4.3 HTML form model
For every `<form>` and orphan input, extract the full constraint model — this is free, precise structure the platform hands you:
- per field: `name`, `type`, label (associated `<label>` / `aria-label` / placeholder / adjacent text), `required`, `pattern`, `min`/`max`/`maxlength`, `step`, `accept`, `autocomplete`, options for `select`/`datalist`/radio groups;
- form-level: `action`, `method`, `enctype`, and **hidden fields** (CSRF tokens, state) — record these as `passthrough` params the executor must echo back verbatim.

The form model often makes probing unnecessary: a standard `GET`/`POST` form *is* an HTTP action contract already.

### A.4.5 Static API discovery (the biggest no-probe win)
Before probing anything, look for sources that describe the site's actions *without execution*. When found, they cover whole swaths of the action set at T0 with high confidence:
- **OpenAPI / Swagger** — fetch common paths (`/openapi.json`, `/swagger.json`, `/api-docs`, `/v3/api-docs`). A hit gives you every endpoint, method, and typed parameter — a complete action surface, for free.
- **GraphQL introspection** — if a `/graphql` endpoint exists and introspection is enabled, one introspection query yields the full typed schema of queries and mutations.
- **Static JS analysis** — scan loaded bundles for `fetch(`/`axios`/`XMLHttpRequest` call sites and URL string patterns to recover candidate endpoints. Minified, dynamically-built URLs make this *lossy*, so treat results as a **hint** (set `needs_full_browser_hint=False` when an element clearly maps to a discovered call; raise confidence; narrow what you'd otherwise probe) — never as sole ground truth.
- **Sitemaps / RSS / feeds** — cheap structured navigation targets.

Map anything discovered here into `discovered_api` on the matching candidate so the router sends it to **T0** (verify by HTTP replay, no browser). This is the single highest-leverage way to avoid probing: a site with an OpenAPI spec barely needs the browser at all.

### A.4.6 Vision residual (optional, last resort)
For interactive widgets that are canvas-rendered, icon-only with no accessible name, or otherwise opaque to AX/DOM, capture the element bbox and run a VLM grounding pass to label it. Keep this rare and cached — it's slow and costs tokens. Most well-built sites won't need it; treat a high VLM-residual rate as a signal the page is hostile and lower overall confidence.

## A.5 Stage 3 — Interaction probing (the core technique)

For each candidate whose effect isn't already certain (i.e. everything except declared interfaces and clean static forms), **find out what it does by doing it, in isolation, and watching.**

Mechanics:
1. Take a fresh, disposable browser **context** cloned from the rendered page state (same cookies/storage), so probing one action can't corrupt another's starting point. For destructive-looking candidates (text contains delete/buy/pay/confirm, or `method=POST` to a sensitive path), **do not execute** — mark `verification="unverified"`, `effect="presumed_mutating"`, `confidence` capped low, and require explicit opt-in at execution time.
2. Arm three observers before triggering:
   - **Network** via CDP `Network.enable` → record `Network.requestWillBeSent` (method, URL, headers, postData) and the matching response status/type.
   - **Navigation** via frame/load events.
   - **DOM mutation** via a `MutationObserver` injected into the page, summarized (nodes added/removed, which region changed, did a dialog open).
3. Trigger the candidate (click, or fill representative values + submit for forms).
4. Classify the observed **EffectClass**:

```
NAVIGATE          → went to a new URL            (record target URL template)
XHR_MUTATING      → POST/PUT/PATCH/DELETE fired  (record endpoint + payload shape)
XHR_QUERY         → GET fired, content updated   (record endpoint + query params)
DOM_LOCAL         → in-page change, no network    (expand/collapse, tab, filter UI)
DIALOG            → opened modal/menu             (record follow-on candidates)
NONE / DEAD       → nothing observed              (drop or mark dead)
```

5. Roll back: discard the probe context. Nothing persists.

```python
async def probe(candidate, base_state, policy) -> ProbeResult:
    if policy.is_destructive(candidate):
        return ProbeResult(effect="presumed_mutating", verified=False, requests=[])
    ctx = await pool.clone_context(base_state)          # isolated, disposable
    cdp = await ctx.new_cdp_session(ctx.page)
    await cdp.send("Network.enable")
    requests, nav, dom = arm_observers(cdp, ctx.page)   # collectors
    try:
        await trigger(ctx.page, candidate)              # click or fill+submit
        await settle(ctx.page, timeout=4000)
    finally:
        snapshot = collect(requests, nav, dom)
        await pool.discard(ctx)
    return classify_effect(snapshot)
```

**Why this is the "works for sure" move:** after probing you no longer *believe* what an element does — you've *seen* it. A "Next" link that turns out to fire `GET /api/items?page=2` becomes a clean, parameterized HTTP action you can execute forever without a browser. A button that only mutates the DOM is correctly typed as a browser action with a verified selector. Dead/no-op elements are dropped instead of polluting the action set. This is also how you get *parameters right*: the observed request payload tells you exactly which fields the backend actually consumes.

Cost control: probing is expensive, so (a) only probe candidates that survived dedup and look useful, (b) probe in parallel across cloned contexts with a concurrency cap, (c) **cache the whole result per template fingerprint** (A.9) so you probe a given page *type* once, not every request, and (d) expose a `actions: "static" | "verified"` request flag so callers who want speed can skip probing and accept lower-confidence inferred actions.

## A.6 Stage 4 — API lifting

When probing reveals an underlying endpoint (`XHR_*` or a form `POST`), **prefer the HTTP expression of the action over the DOM expression.** Record:
- method, URL template (parameterize the parts that came from inputs), required headers (auth, content-type), and which observed payload fields map to which candidate parameters;
- whether auth is cookie/session-based (replay via stored session) or token-based.

Mark the action `execution="http"`. These are gold: fast, browser-free, and stable across redesigns because backend APIs change far less often than front-end markup. Keep the DOM execution path as the **fallback** on the same action (if the API later 4xx/5xx's or shape-drifts, fall back to driving the UI), so the action self-heals across *both* layers.

## A.7 Stage 5 — Parameter schema synthesis

Produce a strict JSON Schema per action from the union of:
- form constraints (types → JSON types; `required`; `pattern` → `pattern`; option lists → `enum`; `min/max/maxlength` → bounds; `type=email/url/date` → `format`);
- observed request payloads from probing (fills in fields the static form missed, e.g. JSON bodies built by JS);
- sensible descriptions (label text, then LLM-polished in A.8).

Strict schemas matter because the agent calling your action will generate arguments from them — `enum`s and `required` flags prevent a whole class of failed invocations. Include `examples` drawn from the probe (real values that worked).

## A.8 Stage 6 — Self-healing locator bundle

Never store a single selector. Store a ranked **bundle** plus a semantic fingerprint, and resolve at execution time by trying them in order, re-grounding against the live AX tree if all fail.

Ranking (most stable first):
1. **Role + accessible name** — `getByRole("button", name="Add to cart")`. Survives styling/structure changes.
2. **Test/semantic attributes** — `data-testid`, `data-test`, `id` if it looks stable (not a hashed build id).
3. **ARIA / label associations** — `getByLabel(...)`, `aria-label`.
4. **Anchored structural path** — short relative path from the nearest stable landmark/ancestor, not a brittle absolute nth-child chain from `<body>`.
5. **Semantic fingerprint** — `{role, accessible_name, region, neighbor_text[]}` used to *re-find* the node via the AX tree when 1–4 miss (and, as a last resort, a VLM "find the element that does X" call).

```python
@dataclass
class LocatorBundle:
    by_role: tuple[str, str] | None        # (role, accessible_name)
    by_testid: str | None
    by_label: str | None
    structural: str | None                 # anchored relative path
    fingerprint: SemanticFingerprint        # always present, for re-grounding

async def resolve(page, bundle) -> Locator:
    for strat in (bundle.by_role, bundle.by_testid, bundle.by_label, bundle.structural):
        loc = try_locator(page, strat)
        if await loc and await loc.count() == 1:
            return loc
    return await reground_via_ax(page, bundle.fingerprint)   # self-heal; may call VLM
```

When re-grounding succeeds, **write the repaired locator back** to the cached action (with a lowered confidence until it's verified again) — the system gets more robust the more it runs.

## A.9 Stage 8 — Template fingerprinting & caching

Pages of the same type (every product page, every search-results page) share an action structure. Compute a **template fingerprint** — a hash of the AX-tree shape (roles + landmark structure, *not* text content) plus the URL path pattern — and cache the fully extracted, probed, verified action set keyed by `(domain, template_fingerprint)`.

Effect: you pay the expensive probing + LLM enrichment **once per page template**, then serve thousands of same-template pages from cache, only re-resolving locators (cheap) and re-binding parameters per specific URL. This is what makes verified extraction economically viable at scale. Invalidate on a TTL and on locator-resolution failure rate crossing a threshold (the template changed).

## A.10 Confidence model

Every action ships with a score and status so agents can gate behavior:

```
confidence = f(
   source_fidelity,        # webmcp > discovered_api > ax_role > structural
   verification_status,    # verified(probed & succeeded) > inferred > unverified
   locator_strength,       # role+name > testid > structural
   schema_completeness,    # all params typed & constrained?
   template_cache_age,     # fresher = higher
)
verification ∈ { verified, inferred, unverified }
effect       ∈ { navigate, query, mutating, presumed_mutating, local, dialog }
```

Surface these in the response. Recommend (and default) that agents auto-invoke only `verified` non-mutating actions, and require explicit human/agent opt-in for `mutating`/`presumed_mutating` or `unverified` actions. This is both a safety feature and a selling point.

## A.11 The action graph (chaining state)

Actions aren't independent — `search` → results page exposes `open_result` → detail page exposes `add_to_cart` → exposes `checkout`. Model the output as a typed graph, not a flat list:
- nodes = page states (keyed by template fingerprint);
- edges = actions, annotated with their `EffectClass` and the state they transition to (learned from probing's observed navigation/DOM result).

Persist this per domain; it accumulates into a **site map of capabilities** that lets an agent (and your own planner) look ahead — "to checkout, I must first add_to_cart, which requires being on a product page." Over time this learned graph is a real moat: a verified, queryable map of what every site can do.

## A.12 Concrete `Action` output (superset of §7)

```jsonc
{
  "id": "act_9b2c",
  "name": "search_products",
  "description": "Search the catalog by keyword and return matching products.",
  "parameters": { "type": "object",
    "properties": { "query": { "type": "string", "description": "Search keywords",
                               "examples": ["wireless headphones"] } },
    "required": ["query"] },
  "execution": "http",
  "http": { "method": "GET", "url_template": "https://x.com/api/search?q={query}",
            "auth": "cookie", "passthrough": [] },
  "fallback_execution": "browser",
  "locator": { "by_role": ["searchbox", "Search products"], "fingerprint": {...} },
  "effect": "query",
  "verification": "verified",
  "confidence": 0.96,
  "leads_to": "tpl_results_3f1a"          // action-graph edge
}
```

## A.13 Failure modes and how each is handled

| Failure | Handling |
|---|---|
| JS widget invisible to AX/DOM | VLM residual pass (A.4.4); if still unresolved, omit + log gap |
| Selector breaks after redesign | Self-healing bundle → re-ground via AX fingerprint → VLM last resort; write back |
| Backend API shape changes | HTTP action falls back to its `browser` execution path; re-probe; re-cache |
| Destructive action | Never auto-probed; `presumed_mutating`; explicit opt-in required to execute |
| CSRF / hidden state | Captured as `passthrough` params; executor re-fetches fresh token before submit |
| Anti-bot blocks probe | Escalate context (proxy/stealth); if still blocked, downgrade to `inferred`, no false promise |
| Infinite/duplicated nav links | Dedup by fingerprint; rank; cap count per page |
| Template drifts silently | Cache invalidation on resolution-failure-rate threshold triggers re-extraction |

The throughline: **every failure degrades to a lower-confidence, clearly-labeled state or a recovery attempt — never to a confident wrong answer.**

## A.14 How to build this engine (phased)

1. **Deterministic core (week 1–2):** AX-tree + form-model harvesting → candidates → flat `Action[]` with role+name locators and form-derived schemas. No probing yet. Confidence = inferred. *This already beats naive DOM scraping.*
2. **Probing + API lifting (week 3–5):** isolated probe contexts, network/nav/DOM observers, EffectClass classification, HTTP lifting. Add `verified` status and the destructive-action guard. *This is the reliability jump — prioritize it.*
3. **Self-healing + fingerprint cache (week 6–7):** locator bundles, AX re-grounding, template fingerprinting + cache. *This is the scale/cost unlock.*
4. **Semantic + graph layer (week 8+):** LLM naming/describing/ranking (cached per template), action graph accumulation, optional VLM residual, WebMCP/manifest ingestion.

Build order is deliberately reliability-first then cost-second then polish-last, because a verified-but-slow action set is a product, and a fast-but-unreliable one is a liability.

## A.15 Why this is the right method (summary)

- It **prefers the most stable signal available** at every step (declared interface → discovered API → AX role → structure), so it degrades gracefully instead of cliff-failing.
- It **verifies empirically** instead of guessing, which is the only way to make trustworthy promises about an arbitrary page.
- It **promotes UI clicks to API calls** wherever possible, making the common case fast, cheap, and durable.
- It **self-heals** at execution time and **caches per template**, making it both robust and economical at scale.
- It **never lies**: confidence + verification + effect labels let agents act safely on what's trustworthy and avoid what isn't.

That combination — not any single clever detector — is what makes action extraction reliable enough to build a product on.

---

## 15. Locked design decisions (Phase 0)

These decisions unblock implementation. Change only with explicit review.

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Default `actions` mode | `fast` | Ship inferred actions quickly; production agents opt into `verified` |
| D2 | T1 light DOM (jsdom) | **Deferred post-v1** | v1 ships T-ignore → T0 → T2 only; avoids Node sidecar complexity |
| D3 | API sync model (v1) | **Fully synchronous** | No job queue until p99 render breaks SLA; add async at M3 |
| D4 | Browser hosting (dev) | Local Playwright pool | Managed provider (Browserbase etc.) for public demos only |
| D5 | Browser hosting (prod demo) | Managed browser provider | Avoid running Chromium fleet before product validation |
| D6 | Target v1 customer | **API-first developers** | MCP ships at M2.5 after REST act loop is proven |
| D7 | Domain policy (dev) | Allowlist per API key | Prevents accidental open-proxy abuse during build |
| D8 | Domain policy (prod) | Open fetch + robots.txt + per-domain rate limits | Product requirement for general-purpose agent API |
| D9 | Package / module name | `web4ai` | Matches repo; layout under `web4ai/` not `agent_ready_api/` |
| D10 | `action_id` TTL | 15 min browser / 1 hr HTTP-verified | Balance freshness vs agent chaining |
| D11 | `actions_profile` default | `minimal` until M1.5, then `full` | Agents get confidence fields when probing ships |

---

## 16. Reconciled implementation plan

Single timeline merging §11 milestones and Appendix A.14 engine phases.

```
Phase 0   Design lock + schemas + golden corpus          3–5 days
M0        Read path (markdown only)                      1–2 weeks
M1        Inferred actions (AX + forms, no probing)      2 weeks
M1.5      Verified actions (probe + API lift)              2–3 weeks   ← differentiator
M2        Execution loop (HTTP → browser sessions)         2 weeks
M2.5      MCP server + framework adapter stub              1 week
M3        Fingerprint cache, async jobs, metering          ongoing
```

### Phase 0 — Design lock (no product code)

| Deliverable | Location |
|---|---|
| JSON Schemas | `schemas/*.json` |
| Golden corpus spec | `golden/corpus.yaml` |
| Locked decisions | §15 above |
| Local dev topology | `docker compose`: API + Redis + Playwright |

### M0 — Read path

**In:** `POST /v1/extract` with `actions: none` → `{markdown, meta}`  
**Out:** queue, billing, proxies, action detection

**Exit:** p50 < 600 ms static on corpus; p50 < 4 s rendered on `spa_shell`; cache hit < 50 ms; token counts ±5%

### M1 — Inferred actions

**In:** AX harvest + form model → flat `Action[]`, `actions: fast`, `verification: inferred`  
**Out:** probing, `POST /act`

**Exit:** ≥ 80% action-name precision vs `golden/labels/`; ≤ 15 actions/page; every action has JSON Schema + `execution`

### M1.5 — Verified actions

**In:** sandbox probing, EffectClass, API lifting, confidence + verification labels, destructive guard  
**Out:** `POST /act`

**Exit:** ≥ 70% corpus actions reach `verified`; ≥ 50% form/search promoted to HTTP where applicable; zero false `verified` on destructive controls

### M2 — Execution loop

**In:** `POST /v1/act` — HTTP first, then browser sessions with `session_id`  
**Out:** crawl, proxies

**Exit:** `e2e_search_flow` and `e2e_pagination_flow` in corpus pass on live URLs

### M2.5 — MCP + launch

**In:** MCP `read_page` + `do_action`; LangChain adapter stub  
**Exit:** demo script runnable via MCP stdio client

### M3 — Scale (ongoing)

Template fingerprint cache, LLM naming (cached per template), async `GET /jobs/{id}`, usage metering, proxy escalation.

---

## 17. Data model (Postgres)

```sql
-- extractions: one row per extract/act result
CREATE TABLE extractions (
  id            TEXT PRIMARY KEY,          -- ext_...
  url           TEXT NOT NULL,
  final_url     TEXT NOT NULL,
  options_hash  TEXT NOT NULL,
  markdown      TEXT,
  meta_json     JSONB NOT NULL,
  strategy      TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX extractions_cache ON extractions (options_hash, url);

-- actions: immutable contracts; act resolves latest non-expired row for action_id
CREATE TABLE actions (
  id              TEXT PRIMARY KEY,        -- act_...
  extraction_id   TEXT REFERENCES extractions(id),
  name            TEXT NOT NULL,
  contract_json   JSONB NOT NULL,          -- full ActionFull blob
  verification    TEXT NOT NULL,
  confidence      REAL NOT NULL,
  expires_at      TIMESTAMPTZ NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX actions_lookup ON actions (id, expires_at DESC);

-- sessions: stateful browser contexts for multi-step flows
CREATE TABLE sessions (
  id                  TEXT PRIMARY KEY,    -- sess_...
  customer_id         TEXT NOT NULL,
  browser_context_ref TEXT,                -- opaque pool handle
  storage_state_json  JSONB,
  last_url            TEXT,
  expires_at          TIMESTAMPTZ NOT NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- usage_events: billing from day one (stub pricing in v1)
CREATE TABLE usage_events (
  id            TEXT PRIMARY KEY,
  customer_id   TEXT NOT NULL,
  endpoint      TEXT NOT NULL,
  timings_json  JSONB,
  cost_units    REAL NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Invariants:**

- `action_id = H(domain, template_fingerprint, semantic_key, contract_version)` — stable across re-extracts of the same template.
- Actions are **immutable** once written; re-extract creates new rows.
- `act` loads contract by `action_id`; on locator failure → re-ground → on failure → `409 action_stale` + fresh extract in error `details`.

---

## 18. API error contract

| HTTP | `error` code | When |
|---|---|---|
| 400 | `invalid_request` | Bad URL, schema validation failure, unknown param |
| 400 | `domain_not_allowed` | URL domain not on key allowlist |
| 402 | `quota_exceeded` | Usage cap hit (stub in v1) |
| 403 | `mutating_not_confirmed` | `requires_confirm` action without `confirm_mutating: true` |
| 404 | `action_not_found` | Unknown or expired `action_id` |
| 409 | `action_stale` | Contract no longer resolves on live page |
| 422 | `unextractable` | Fetch succeeded but zero extractable content |
| 429 | `rate_limited` | Per-customer or per-target-domain limit |
| 502 | `target_blocked` | WAF, CAPTCHA, connection refused |
| 504 | `render_timeout` | Browser settle exceeded `timeout_ms` |
| 500 | `internal_error` | Unexpected failure |

All errors return `ApiError` schema (`schemas/common.json#/$defs/ApiError`) with `request_id` for support.

---

## 19. v1 scope fence

### In v1

- Sync `POST /v1/extract` and `POST /v1/act`
- Tiers: T-ignore, T0 HTTP, T2 browser probe
- AX + form harvesting; probing + API lift (M1.5)
- Self-healing locator bundles (basic re-ground via AX)
- Redis cache (URL + domain JS hints)
- API key auth + domain allowlist
- MCP stdio server (M2.5)
- Golden corpus regression tests

### Explicitly deferred

| Item | Phase |
|---|---|
| T1 light DOM (jsdom/happy-dom) | post-v1 |
| VLM residual grounding | M3+ |
| Action graph / site capability map (A.11) | M3+ |
| `POST /v1/crawl` | M3+ |
| Async job queue + `GET /jobs/{id}` | M3+ |
| Rust markdown / static-fetch workers | M4 |
| Proxy / anti-bot escalation | M3 |
| Logged-in / checkout flows | post-v1 (legal review) |
| WebMCP manifest ingestion | stub interface only; full parser M3+ |

---

## 20. Action contract schema

Canonical JSON Schemas live in `schemas/`. Two response profiles:

### Minimal (`actions_profile: minimal`) — M1 default

```jsonc
{
  "id": "act_7f3a2b1c9d0e",
  "name": "search_products",
  "description": "Search the product catalog by keyword.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "description": "Search keywords" }
    },
    "required": ["query"]
  },
  "execution": "http"
}
```

### Full (`actions_profile: full`) — M1.5+ default

```jsonc
{
  "id": "act_9b2c4d5e6f70",
  "name": "search_products",
  "description": "Search the catalog by keyword and return matching products.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Search keywords",
        "examples": ["wireless headphones"]
      }
    },
    "required": ["query"]
  },
  "execution": "http",
  "verification": "verified",
  "confidence": 0.96,
  "effect": "query",
  "source_url": "https://books.toscrape.com/",
  "template_fingerprint": "tpl_a1b2c3d4",
  "expires_at": "2026-06-10T13:00:00Z",
  "tier": "t0_http",
  "source": "probe",
  "rank": 1,
  "requires_confirm": false,
  "http": {
    "method": "GET",
    "url_template": "https://books.toscrape.com/catalogue/page-1.html?search={query}",
    "auth": "none",
    "passthrough": []
  },
  "fallback_execution": "browser",
  "browser": {
    "interaction": "fill_and_submit",
    "locator": {
      "by_role": ["searchbox", "Search"],
      "fingerprint": {
        "role": "searchbox",
        "accessible_name": "Search",
        "region": "main"
      }
    },
    "fill_map": { "query": "input[name='q']" }
  },
  "locator": {
    "by_role": ["searchbox", "Search"],
    "fingerprint": {
      "role": "searchbox",
      "accessible_name": "Search",
      "region": "main"
    }
  },
  "leads_to": "tpl_results_3f1a"
}
```

**Execution path resolution at `act` time:**

```
1. Load ActionFull by action_id (must not be expired)
2. If execution == "http" → build request from http contract + params + passthrough
   → on 4xx/5xx AND fallback_execution == "browser" → fall through to step 3
3. If execution == "browser" OR fallback triggered:
   → get/create session → resolve locator bundle → interact → capture page
4. Run full extract pipeline on result page → return ExtractResponse
```

Schema files: `schemas/action.json`, `schemas/extract-request.json`, `schemas/extract-response.json`, `schemas/act-request.json`.

---

## 21. Probing protocol

Normative spec for M1.5. Implements Appendix A.5 with operational detail.

### 21.1 Preconditions

Probing runs only when:

- `actions: verified` on the request
- Candidate survived T-ignore dedup
- Candidate is not classified destructive (see §21.4)
- Candidate tier is T2 (or T0 replay failed and escalated)

Skipped candidates remain `verification: inferred`.

### 21.2 Probe context lifecycle

Each probe is **fully isolated** — no state leaks between candidates.

```
base_state = rendered page storage_state + cookies + final_url
for each candidate in probe_queue:
    ctx = pool.clone_context(base_state)     # fresh context, same cookies
    page = ctx.page at base_state.final_url
    cdp  = new_cdp_session(page)
    arm_observers(cdp, page)
    try:
        trigger(page, candidate, representative_params)
        await settle(page, timeout=4000ms)
    finally:
        snapshot = collect_observers()
        pool.discard(ctx)                    # never returned to pool
```

**Rollback guarantee:** `discard(ctx)` closes the context and all pages. No cookies, localStorage, or DOM mutations from probe N are visible to probe N+1 or the returned action set. The `base_state` is captured once before the probe batch and never mutated.

### 21.3 Observers

Three observers arm **before** trigger and collect **after** settle.

#### Network (CDP)

```python
await cdp.send("Network.enable")
# Listen: Network.requestWillBeSent, Network.responseReceived
# Record per request: requestId, method, url, headers, postData, status, mimeType
```

Filter out: analytics domains (google-analytics, segment, etc.), static assets (`.js`, `.css`, `.png`, fonts), beacons < 200 bytes.

#### Navigation

```python
# Listen: framenavigated (main frame only), Page.loadEventFired
# Record: from_url, to_url, navigation_type (link | form | history | reload)
```

#### DOM mutation (injected script)

```javascript
// Injected once per probe context before trigger
(() => {
  const summary = { added: 0, removed: 0, dialog_opened: false, region: null };
  const obs = new MutationObserver((mutations) => {
    for (const m of mutations) {
      summary.added += m.addedNodes.length;
      summary.removed += m.removedNodes.length;
    }
    const dlg = document.querySelector('[role=dialog], dialog[open]');
    if (dlg) { summary.dialog_opened = true; summary.region = 'dialog'; }
  });
  obs.observe(document.body, { childList: true, subtree: true });
  window.__probeSummary = () => { obs.disconnect(); return summary; };
})();
```

### 21.4 Destructive-action guard

Never auto-probe when any rule matches:

| Rule | Example |
|---|---|
| Accessible name matches `/buy|purchase|pay|delete|confirm|submit order/i` | "Buy now" |
| `method=POST` to path matching `/checkout|payment|order|delete/i` | Checkout form |
| `type=submit` inside form with `effect` unknown + price visible nearby | Add to cart (mark `presumed_mutating`) |
| Policy flag `is_destructive(candidate)` from customer config | Custom blocklist |

Result: `verification: unverified`, `effect: presumed_mutating`, `requires_confirm: true`, `confidence ≤ 0.3`. No trigger executed.

### 21.5 Representative parameters

Forms need plausible values to produce meaningful network traffic:

| Field type | Representative value |
|---|---|
| `search`, `text`, `q` | `"test"` or fixture-specific from `golden/corpus.yaml` |
| `email` | `probe@web4ai.dev` |
| `number` | midpoint of min/max or `1` |
| `date` | today's ISO date |
| `select` | first non-empty option |
| `checkbox` | `true` if required, else skip |
| Hidden / passthrough | echo from DOM verbatim |

### 21.6 EffectClass classification

Priority order (first match wins):

```
1. NAVIGATE     — main-frame URL changed
2. XHR_MUTATING — POST/PUT/PATCH/DELETE to non-static endpoint
3. XHR_QUERY    — GET or POST returning JSON/HTML fragment; DOM changed; URL unchanged
4. DIALOG       — dialog_opened; no navigation
5. DOM_LOCAL    — mutation summary added+removed > 0; no navigation; no XHR
6. DEAD         — nothing observed within settle window
```

Map to `effect` field: `navigate`, `mutating`, `query`, `dialog`, `local`, `dead`.

`DEAD` candidates are dropped from the action set (logged in `suppressed` when debug enabled).

### 21.7 API lifting rules

After classification, promote to HTTP when:

- `XHR_QUERY` or `XHR_MUTATING` captured a request with parseable URL + body
- OR standard form model has `action` + `method` and probe confirmed navigation/XHR

Build `http.url_template` by replacing observed param values with `{param_name}` placeholders. Store observed headers minus hop-by-hop (`Host`, `Content-Length`). Set `auth: cookie` when probe request included session cookies.

Always retain `browser` contract as `fallback_execution`.

### 21.8 Parallelization and cost caps

| Parameter | Default | Notes |
|---|---|---|
| `max_concurrent_probes` | 4 per worker | Across disposable contexts |
| `max_probes_per_page` | 20 | After rank; skip lowest-rank inferred |
| `probe_timeout_ms` | 4000 | Per candidate |
| `probe_batch_timeout_ms` | 30000 | Total for all probes on one page |

When `probe_batch_timeout_ms` exceeded: remaining candidates stay `inferred`; response includes `meta.probed_count` < queue size.

### 21.9 Probe result → Action fields

```python
@dataclass
class ProbeResult:
    effect_class: EffectClass
    verified: bool                    # True unless destructive skip or DEAD
    requests: list[CapturedRequest]
    navigation: NavigationEvent | None
    dom_delta: DomDelta
    observed_params: dict[str, Any]   # fields actually sent
```

`verified = True` only when effect_class ≠ DEAD and observers fired expected signals on retry (single confirm probe for ambiguous `DOM_LOCAL`).

---

## 22. Golden corpus

Regression fixtures defined in `golden/corpus.yaml`. **12 fixtures** covering M0–M2.

| ID | URL | Milestone | Validates |
|---|---|---|---|
| `static_article` | news.ycombinator.com/item?id=1 | M0 | Static markdown, no script leakage |
| `docs_page` | developer.mozilla.org (HTTP GET) | M0 | Headings, code fences |
| `wikipedia_article` | en.wikipedia.org/wiki/Web_scraping | M0 | Long article, tables |
| `books_listing` | books.toscrape.com | M1 | Listing markdown + search + pagination actions |
| `quotes_pagination` | quotes.toscrape.com/page/1 | M1 | `go_to_next_page` HTTP contract |
| `get_search_form` | books.toscrape.com | M1.5 | Verified GET search → HTTP lift |
| `post_form_csrf` | httpbin.org/forms/post | M1.5 | Passthrough hidden fields, mutating guard |
| `spa_shell` | react.dev | M0 | Render path, substantive post-JS markdown |
| `cookie_banner_heavy` | bbc.com | M1 | T-ignore suppresses consent noise |
| `ajax_search` | quotes.toscrape.com/search | M1.5 | XHR capture, optional API lift |
| `in_page_tab` | w3schools.com (tabs demo) | M1.5 | `DOM_LOCAL` / `effect: local` |
| `e2e_search_flow` | books.toscrape.com | M2 | extract → act → results markdown |
| `e2e_pagination_flow` | quotes.toscrape.com/page/1 | M2 | extract → act → page 2 URL |

### Snapshot workflow (Phase 0b)

```bash
# Capture frozen HTML for offline unit tests (run once, commit to golden/snapshots/)
python -m web4ai.golden.capture --fixture books_listing
python -m web4ai.golden.capture --fixture spa_shell --render always
```

Unit tests load snapshots only (no network). Integration job hits live URLs weekly.

### Human labels (M1 precision)

For each M1 fixture, `golden/labels/{fixture_id}.json` lists expected actions:

```jsonc
{
  "actions": [
    { "name": "search_products", "execution": "http", "required_params": ["query"] },
    { "name": "go_to_next_page", "execution": "http", "required_params": [] }
  ]
}
```

Precision = |predicted ∩ labeled| / |predicted|; target ≥ 0.80 at M1 exit.

---

## 23. MCP tool surface

Full definition: `schemas/mcp-tools.json`. Ships at **M2.5** after REST act loop works.

### Tools

| Tool | REST equivalent | Purpose |
|---|---|---|
| `read_page` | `POST /v1/extract` | Markdown + actions for a URL |
| `do_action` | `POST /v1/act` | Execute action → new page state |
| `search_site` | extract + act composite | Convenience: find search action, run query |

### Auth

| Transport | Method |
|---|---|
| REST | `Authorization: Bearer w4a_<key>` |
| MCP stdio | `WEB4AI_API_KEY` env var |
| MCP SSE | Same Bearer header on `/mcp/sse` (TLS required) |

**Key scopes:** `extract:read`, `act:execute`, `act:mutating` (required for `confirm_mutating` on destructive actions).

**Domain allowlist:** enforced per key from D7/D8. Empty allowlist = deny all in dev.

### MCP client config example (Cursor / Claude Desktop)

```jsonc
{
  "mcpServers": {
    "web4ai": {
      "command": "python",
      "args": ["-m", "web4ai.mcp"],
      "env": {
        "WEB4AI_API_KEY": "w4a_...",
        "WEB4AI_BASE_URL": "http://localhost:8000"
      }
    }
  }
}
```

### Agent loop (normative)

```
1. read_page(url) → markdown + actions + session_id
2. Agent picks action by name/description/confidence
3. do_action(action_id, params, session_id) → new markdown + actions
4. Repeat until goal met or no high-confidence actions remain
```

Recommend agents auto-invoke only `verification: verified` + `requires_confirm: false`. Prompt user before `requires_confirm: true`.

---

## 24. Updated project structure

```
web4ai/
├── web4ai/                  # Python package (was agent_ready_api/)
│   ├── api/
│   │   ├── routes/extract.py
│   │   ├── routes/act.py
│   │   └── schemas.py       # Pydantic models generated from / validated against schemas/
│   ├── pipeline/
│   │   ├── router.py
│   │   ├── fetch.py
│   │   ├── render.py
│   │   ├── extract.py
│   │   ├── markdown.py
│   │   ├── actions/
│   │   │   ├── harvest.py   # AX + forms + declared interfaces
│   │   │   ├── route.py     # T-ignore / T0 / T2 tier router
│   │   │   ├── probe.py     # §21 probing protocol
│   │   │   ├── lift.py      # API lifting
│   │   │   ├── contract.py
│   │   │   └── enrich.py    # LLM naming (M3)
│   │   └── assemble.py
│   ├── execution/
│   │   ├── http_action.py
│   │   ├── browser_action.py
│   │   └── sessions.py
│   ├── mcp/
│   │   └── server.py
│   ├── infra/
│   │   ├── cache.py
│   │   ├── browser_pool.py
│   │   └── db.py
│   └── golden/
│       ├── capture.py       # snapshot capture CLI
│       └── runner.py        # corpus test runner
├── schemas/                 # JSON Schema (source of truth)
├── golden/
│   ├── corpus.yaml
│   ├── snapshots/
│   └── labels/
├── design-spec.md
├── docker-compose.yml
└── tests/
```

---

## 25. Phase 0 completion checklist

- [x] Locked design decisions (§15)
- [x] Reconciled milestone plan (§16)
- [x] Postgres data model (§17)
- [x] Error contract (§18)
- [x] v1 scope fence (§19)
- [x] Action JSON Schemas (`schemas/`)
- [x] Probing protocol (§21)
- [x] Golden corpus spec (`golden/corpus.yaml`)
- [x] MCP tool definitions (`schemas/mcp-tools.json`)
- [ ] Capture HTML snapshots (`golden/snapshots/*.html`)
- [ ] Write human labels (`golden/labels/*.json`)
- [ ] `docker-compose.yml` for local dev
- [ ] Begin M0 implementation