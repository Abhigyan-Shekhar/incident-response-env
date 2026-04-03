"""FastAPI application entrypoint aligned with the official OpenEnv examples."""

from __future__ import annotations

from incident_response_env.server.app import app


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
