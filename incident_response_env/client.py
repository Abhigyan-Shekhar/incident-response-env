from __future__ import annotations

from dataclasses import dataclass

import requests

from .models import IncidentAction, IncidentObservation, IncidentState


@dataclass
class ClientResult:
    observation: IncidentObservation
    reward: float | None
    done: bool


class IncidentResponseEnvClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()

    def close(self) -> None:
        self._session.close()

    def reset(self, difficulty: str = "easy") -> ClientResult:
        response = self._session.post(
            f"{self.base_url}/reset",
            json={"difficulty": difficulty},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return ClientResult(
            observation=IncidentObservation.model_validate(data["observation"]),
            reward=data.get("reward"),
            done=bool(data.get("done")),
        )

    def step(self, action: IncidentAction) -> ClientResult:
        response = self._session.post(
            f"{self.base_url}/step",
            json={"action": action.model_dump(exclude_none=True)},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return ClientResult(
            observation=IncidentObservation.model_validate(data["observation"]),
            reward=data.get("reward"),
            done=bool(data.get("done")),
        )

    def state(self) -> IncidentState:
        response = self._session.get(f"{self.base_url}/state", timeout=30)
        response.raise_for_status()
        return IncidentState.model_validate(response.json())
