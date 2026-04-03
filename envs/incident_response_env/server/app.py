"""OpenEnv-style app entrypoint for the incident response environment."""

from __future__ import annotations

from server.app import app


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


__all__ = ["app", "main"]
