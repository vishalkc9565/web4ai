# web4AI

Extract clean Markdown and structured action sets from any URL — built for AI agents.

## Quick start

```bash
make install-dev
make run          # web4ai dev — reload on :8000
```

```bash
curl -s -X POST http://localhost:8000/v1/extract \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com"}' | jq .
```

## API

- `POST /v1/extract` — extract markdown + actions from a URL
- `GET /health` — health check
- `GET /docs` — OpenAPI spec (Swagger UI)

## Paperclip

This project can be managed with [Paperclip](https://paperclip.ing) (local agent orchestration).

**First-time setup:**

```bash
npx paperclipai onboard --yes
```

**Start the server:**

```bash
npx paperclipai run
```

- Dashboard: http://127.0.0.1:3100
- API health: http://127.0.0.1:3100/api/health

Agents using the Cursor adapter need CLI auth once per machine:

```bash
agent login
agent status   # should show "Logged in as ..."
```

Useful commands: `paperclipai doctor`, `paperclipai configure`.

## Deploy (Cloudflare)

The API runs in a [Cloudflare Container](https://developers.cloudflare.com/containers/) behind a Worker proxy. A TypeScript Worker (`worker/index.ts`) routes traffic; the FastAPI app starts inside the container via `web4ai serve` on `10.0.0.1:8080`.

| Mode | Command | Listens on |
|------|---------|------------|
| Local dev | `make run` / `web4ai dev` | `0.0.0.0:8000` (reload) |
| Local prod-like | `make serve` / `web4ai serve` | `WEB4AI_HOST`:`WEB4AI_PORT` (default `0.0.0.0:8000`) |
| Container / CF | `web4ai serve` in Docker | `10.0.0.1:8080` |
| Cloudflare edge | `npm run deploy` | Worker URL → container |

**Prerequisites:** Docker running locally, Cloudflare account (`npx wrangler login`).

```bash
make install-worker   # npm ci
make deploy           # npm run deploy → wrangler deploy
```

**Cloudflare Workers Builds settings:**

| Field | Command |
|-------|---------|
| Build command | `npm run build` (or `npm ci`) |
| Deploy command | `npm run deploy` |
| Version command | `npm run preview` |

**Workers Builds auth (fixes `Unauthorized` after Docker build):**

1. Open your Worker → **Settings → Build → API token**.
2. Click **Create new token** (or select a custom token with these permissions):
   - Account → **Workers Scripts** → Edit
   - Account → **Containers** → Edit
   - User → **Memberships** → Read
3. `account_id` is already set in `wrangler.jsonc`. Optionally add a build env var `CLOUDFLARE_ACCOUNT_ID=ffcd10abbf1a2bd9ee843c60f1540599`.
4. **Containers require a Workers Paid plan** — Free plan deploys fail at the push step with a generic auth error.

Never use `make run` or `web4ai dev` in CI — those block with reload enabled.

## Development

```bash
make ci      # lint + unit tests
make test-all  # includes integration tests (network)
```
