FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --no-dev --extra browser

COPY src/web4ai ./src/web4ai
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra browser

RUN --mount=type=cache,target=/root/.cache/uv \
    uv run playwright install --with-deps chromium

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT []

EXPOSE 8080
CMD ["uvicorn", "web4ai.api.app:app", "--host", "10.0.0.1", "--port", "8080"]
