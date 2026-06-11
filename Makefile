.PHONY: install install-dev install-worker lint test test-integration dashboard ci run deploy

install:
	uv sync

install-dev:
	uv sync --extra dev --extra browser
	uv run playwright install chromium

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

test:
	uv run pytest tests -q --ignore=tests/test_integration.py

test-all:
	uv run pytest tests -q

test-integration:
	uv run pytest tests/test_integration.py -m integration -q

dashboard:
	uv run python scripts/dashboard/run_corpus.py --api http://127.0.0.1:8000

ci: lint test

run:
	uv run uvicorn web4ai.api.app:app --reload --host 0.0.0.0 --port 8000

install-worker:
	npm ci

deploy:
	npx wrangler deploy
