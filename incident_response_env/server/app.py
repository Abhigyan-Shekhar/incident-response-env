from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

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
    episode_id: str | None = None
    timeout_s: float | None = None


app = FastAPI(title="IncidentResponseEnv", version="0.1.0")
_sessions: dict[str, IncidentResponseEnvironment] = {}
_current_episode_id: str | None = None


def _resolve_env(episode_id: str | None) -> IncidentResponseEnvironment | None:
    global _current_episode_id

    target_episode_id = episode_id or _current_episode_id
    if target_episode_id is None:
        return None
    env = _sessions.get(target_episode_id)
    if env is not None:
        _current_episode_id = target_episode_id
    return env


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
    global _current_episode_id

    env = IncidentResponseEnvironment()
    observation = env.reset(
        seed=body.seed,
        episode_id=body.episode_id,
        difficulty=body.difficulty,
    )
    episode_id = str(observation.metadata["episode_id"])
    _sessions[episode_id] = env
    _current_episode_id = episode_id
    return {
        "observation": observation.model_dump(exclude={"reward", "done", "metadata"}),
        "reward": observation.reward,
        "done": observation.done,
        "metadata": observation.metadata,
    }


@app.post("/step")
def step(body: StepBody) -> dict[str, Any]:
    env = _resolve_env(body.episode_id)
    if env is None:
        observation = IncidentResponseEnvironment().step(body.action, timeout_s=body.timeout_s)
    else:
        observation = env.step(body.action, timeout_s=body.timeout_s)
    return {
        "observation": observation.model_dump(exclude={"reward", "done", "metadata"}),
        "reward": observation.reward,
        "done": observation.done,
        "metadata": observation.metadata,
    }


@app.get("/state")
def state(episode_id: str | None = None) -> dict[str, Any]:
    env = _resolve_env(episode_id)
    if env is None:
        return IncidentState().model_dump()
    return env.state.model_dump()


def main() -> None:
    import uvicorn

    uvicorn.run("server.app:app", host="0.0.0.0", port=8000, reload=False)
