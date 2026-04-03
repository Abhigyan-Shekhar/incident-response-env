"""FastAPI application entrypoint aligned with the official OpenEnv examples."""

from __future__ import annotations

try:
    from openenv.core.env_server.http_server import create_app

    from models import IncidentAction, IncidentObservation
    from server.incident_response_environment import IncidentResponseEnvironment

    app = create_app(
        IncidentResponseEnvironment,
        IncidentAction,
        IncidentObservation,
        env_name="incident_response_env",
    )
except ImportError:
    from incident_response_env.server.app import app


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
