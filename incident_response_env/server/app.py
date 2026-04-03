from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from ..compat import OPENENV_AVAILABLE, openenv_create_app
from ..environment import IncidentResponseEnvironment
from ..models import IncidentAction, IncidentObservation, IncidentState


class ResetBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    seed: int | None = None
    episode_id: str | None = None
    difficulty: str = "easy"


class StepBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    action: IncidentAction
    timeout_s: float | None = None


if OPENENV_AVAILABLE and openenv_create_app is not None:
    app = openenv_create_app(
        IncidentResponseEnvironment,
        IncidentAction,
        IncidentObservation,
        env_name="incident_response_env",
    )
else:
    app = FastAPI(title="IncidentResponseEnv", version="0.1.0")
    _env = IncidentResponseEnvironment()

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "name": "IncidentResponseEnv",
            "description": "Deterministic SRE on-call incident triage environment.",
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/schema")
    def schema() -> dict[str, Any]:
        return {
            "action": IncidentAction.model_json_schema(),
            "observation": IncidentObservation.model_json_schema(),
            "state": IncidentState.model_json_schema(),
        }

    @app.post("/reset")
    def reset(body: ResetBody) -> dict[str, Any]:
        observation = _env.reset(
            seed=body.seed,
            episode_id=body.episode_id,
            difficulty=body.difficulty,
        )
        return {
            "observation": observation.model_dump(exclude={"reward", "done", "metadata"}),
            "reward": observation.reward,
            "done": observation.done,
        }

    @app.post("/step")
    def step(body: StepBody) -> dict[str, Any]:
        observation = _env.step(body.action, timeout_s=body.timeout_s)
        return {
            "observation": observation.model_dump(exclude={"reward", "done", "metadata"}),
            "reward": observation.reward,
            "done": observation.done,
        }

    @app.get("/state")
    def state() -> dict[str, Any]:
        return _env.state.model_dump()


def main() -> None:
    import uvicorn

    uvicorn.run("server.app:app", host="0.0.0.0", port=8000, reload=False)
