#!/usr/bin/env python3
"""Run golden corpus against a live web4AI API and refresh dashboard artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.golden_corpus import fixture_category, validate_fixture  # noqa: E402

DEFAULT_API = "http://localhost:8000"
CORPUS_PATH = ROOT / "golden" / "corpus.yaml"
DASHBOARD_DIR = ROOT / "dashboard" / "testing"
SNAPSHOT_DIR = DASHBOARD_DIR / "snapshots"


def load_corpus() -> tuple[dict, list[dict]]:
    data = yaml.safe_load(CORPUS_PATH.read_text())
    defaults = data.get("defaults") or {}
    fixtures = data.get("fixtures") or []
    return defaults, fixtures


def build_request_body(fixture: dict, defaults: dict) -> dict:
    body: dict = {
        "url": fixture["url"],
        "render": fixture.get("render", defaults.get("render", "auto")),
        "actions": fixture.get("actions", defaults.get("actions", "fast")),
        "include_actions": fixture.get("actions", defaults.get("actions", "fast")) != "none",
        "use_cache": False,
    }
    if fixture.get("render") == "always" or body["render"] == "always":
        body["timeout_ms"] = 45000
    return body


def run_fixture(
    client: httpx.Client, api_base: str, fixture: dict, defaults: dict
) -> dict:
    if fixture.get("e2e"):
        return {
            "id": fixture["id"],
            "name": fixture.get("name", fixture["id"]),
            "url": fixture["url"],
            "category": fixture_category(fixture),
            "status": "skipped",
            "skipped_reason": "e2e multi-step flow not run in dashboard batch",
            "extraction_path": None,
            "action_count": None,
            "markdown_length": None,
            "failures": [],
        }

    body = build_request_body(fixture, defaults)
    url = f"{api_base.rstrip('/')}/v1/extract"
    try:
        resp = client.post(url, json=body)
    except httpx.HTTPError as exc:
        return {
            "id": fixture["id"],
            "name": fixture.get("name", fixture["id"]),
            "url": fixture["url"],
            "category": fixture_category(fixture),
            "status": "error",
            "extraction_path": None,
            "action_count": None,
            "markdown_length": None,
            "failures": [f"HTTP error: {exc}"],
            "snapshot": None,
        }

    if resp.status_code != 200:
        return {
            "id": fixture["id"],
            "name": fixture.get("name", fixture["id"]),
            "url": fixture["url"],
            "category": fixture_category(fixture),
            "status": "error",
            "extraction_path": None,
            "action_count": None,
            "markdown_length": None,
            "failures": [f"HTTP {resp.status_code}: {resp.text[:200]}"],
            "snapshot": None,
        }

    data = resp.json()
    failures = validate_fixture(fixture, data)
    status = "pass" if not failures else "fail"
    snapshot_rel = None
    if status == "fail":
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snap_path = SNAPSHOT_DIR / f"{fixture['id']}.json"
        snap_path.write_text(json.dumps(data, indent=2, default=str))
        snapshot_rel = str(snap_path.relative_to(ROOT))

    return {
        "id": fixture["id"],
        "name": fixture.get("name", fixture["id"]),
        "url": fixture["url"],
        "category": fixture_category(fixture),
        "status": status,
        "extraction_path": (data.get("meta") or {}).get("extraction_path"),
        "action_count": len(data.get("actions") or []),
        "markdown_length": len(data.get("markdown") or ""),
        "failures": failures,
        "snapshot": snapshot_rel,
        "title": data.get("title"),
        "error": (data.get("error") or {}).get("code") if data.get("error") else None,
    }


def render_html(summary: dict) -> str:
    rows = []
    for r in summary["results"]:
        status = r["status"]
        badge_class = {"pass": "green", "fail": "red", "error": "red", "skipped": "yellow"}.get(
            status, "muted"
        )
        failures = "<br>".join(r.get("failures") or []) or "—"
        snap = r.get("snapshot")
        snap_cell = f'<a href="{snap}">{snap}</a>' if snap else "—"
        rows.append(
            f"""<tr>
  <td><code>{r['id']}</code></td>
  <td>{r['category']}</td>
  <td><a href="{r['url']}" target="_blank" rel="noopener">{r['url'][:60]}…</a></td>
  <td><span class="badge {badge_class}">{status}</span></td>
  <td>{r.get('extraction_path') or '—'}</td>
  <td>{r.get('action_count') if r.get('action_count') is not None else '—'}</td>
  <td>{r.get('markdown_length') if r.get('markdown_length') is not None else '—'}</td>
  <td>{r.get('error') or '—'}</td>
  <td class="failures">{failures}</td>
  <td>{snap_cell}</td>
</tr>"""
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>web4AI Golden Corpus Testing Dashboard</title>
  <style>
    :root {{
      --bg: #0f1117; --surface: #1a1d27; --border: #2a2f3d;
      --text: #e8eaed; --muted: #9aa0a6; --accent: #6c9eff;
      --green: #34d399; --yellow: #fbbf24; --red: #f87171;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.5; padding: 2rem; }}
    h1 {{ font-size: 1.75rem; }}
    .subtitle {{ color: var(--muted); margin: 0.5rem 0 1.5rem; }}
    .summary {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }}
    .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
      padding: 1rem 1.25rem; min-width: 140px; }}
    .card .label {{ color: var(--muted); font-size: 0.8rem; }}
    .card .value {{ font-size: 1.5rem; font-weight: 600; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--surface);
      border: 1px solid var(--border); border-radius: 8px; overflow: hidden; font-size: 0.875rem; }}
    th, td {{ padding: 0.65rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border); }}
    th {{ color: var(--muted); font-weight: 500; }}
    .badge {{ padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }}
    .badge.green {{ background: rgba(52,211,153,0.15); color: var(--green); }}
    .badge.red {{ background: rgba(248,113,113,0.15); color: var(--red); }}
    .badge.yellow {{ background: rgba(251,191,36,0.15); color: var(--yellow); }}
    .failures {{ color: var(--red); max-width: 280px; }}
    a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <h1>web4AI Golden Corpus Testing Dashboard</h1>
  <p class="subtitle">API: <code>{summary['api_base']}</code> · Last run: <code>{summary['run_at']}</code></p>
  <div class="summary">
    <div class="card"><div class="label">Passed</div><div class="value" style="color:var(--green)">{summary['passed']}</div></div>
    <div class="card"><div class="label">Failed</div><div class="value" style="color:var(--red)">{summary['failed']}</div></div>
    <div class="card"><div class="label">Skipped</div><div class="value" style="color:var(--yellow)">{summary['skipped']}</div></div>
    <div class="card"><div class="label">Total fixtures</div><div class="value">{summary['total']}</div></div>
  </div>
  <table>
    <thead>
      <tr>
        <th>ID</th><th>Category</th><th>URL</th><th>Status</th>
        <th>Path</th><th>Actions</th><th>Markdown</th><th>Error</th><th>Failing checks</th><th>Snapshot</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
  <p class="subtitle" style="margin-top:1.5rem">Data: <code>dashboard/testing/results.json</code> · Refresh: <code>make dashboard</code></p>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run golden corpus and refresh testing dashboard")
    parser.add_argument("--api", default=DEFAULT_API, help="API base URL")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    health = httpx.get(f"{args.api.rstrip('/')}/health", timeout=10.0)
    health.raise_for_status()

    defaults, fixtures = load_corpus()
    run_at = datetime.now(UTC).isoformat()
    results: list[dict] = []

    with httpx.Client(timeout=args.timeout) as client:
        for fixture in fixtures:
            results.append(run_fixture(client, args.api, fixture, defaults))

    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] in ("fail", "error"))
    skipped = sum(1 for r in results if r["status"] == "skipped")

    summary = {
        "run_at": run_at,
        "api_base": args.api,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    (DASHBOARD_DIR / "results.json").write_text(json.dumps(summary, indent=2))
    (DASHBOARD_DIR / "index.html").write_text(render_html(summary))

    print(f"Dashboard refreshed: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"Artifacts: {DASHBOARD_DIR / 'index.html'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
