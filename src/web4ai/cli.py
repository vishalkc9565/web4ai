"""CLI entry point."""

import uvicorn


def main() -> None:
    uvicorn.run("web4ai.api.app:app", host="0.0.0.0", port=8000, reload=False)
