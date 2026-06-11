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

## Development

```bash
make ci      # lint + unit tests
make test-all  # includes integration tests (network)
```
