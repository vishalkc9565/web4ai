# web4AI

Extract clean Markdown and structured action sets from any URL — built for AI agents.

## Quick start

```bash
make install-dev
make run
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

The API deploys to [Cloudflare Containers](https://developers.cloudflare.com/containers/) via Wrangler. The deploy command builds the Docker image, pushes it, and **exits** — do not use `make run` in CI.

**Prerequisites:** Docker running locally, Cloudflare account (`npx wrangler login`).

```bash
make install-worker   # npm ci — installs wrangler
make deploy           # npx wrangler deploy
```

**Cloudflare Workers Builds settings:**

| Field | Command |
|-------|---------|
| Build command | `npm ci` |
| Deploy command | `npx wrangler deploy` |
| Version command | `npx wrangler versions upload` (optional) |

**Workers Builds auth (fixes `Unauthorized` after Docker build):**

1. Open your Worker → **Settings → Build → API token**.
2. Click **Create new token** (or select a custom token with these permissions):
   - Account → **Workers Scripts** → Edit
   - Account → **Containers** → Edit
   - User → **Memberships** → Read
3. `account_id` is already set in `wrangler.jsonc`. Optionally add a build env var `CLOUDFLARE_ACCOUNT_ID=ffcd10abbf1a2bd9ee843c60f1540599`.
4. **Containers require a Workers Paid plan** — Free plan deploys fail at the push step with a generic auth error.

Local dev remains `make run` (uvicorn on port 8000).

## Development

```bash
make ci      # lint + unit tests
make test-all  # includes integration tests (network)
```
