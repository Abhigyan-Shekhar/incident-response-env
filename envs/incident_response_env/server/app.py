"""OpenEnv-style app entrypoint for the incident response environment."""

from __future__ import annotations

try:
    from openenv.core.env_server.http_server import create_app

    from envs.incident_response_env.models import IncidentAction, IncidentObservation
    from envs.incident_response_env.server.incident_response_environment import (
        IncidentResponseEnvironment,
    )

    app = create_app(
        IncidentResponseEnvironment,
        IncidentAction,
        IncidentObservation,
        env_name="incident_response_env",
    )
except ImportError:
    from server.app import app


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


__all__ = ["app", "main"]
