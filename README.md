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

## Development

```bash
make ci      # lint + unit tests
make test-all  # includes integration tests (network)
```
