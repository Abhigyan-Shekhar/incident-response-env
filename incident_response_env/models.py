from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import Field, model_validator

from .compat import OpenEnvAction, OpenEnvObservation, OpenEnvState

ActionType = Literal[
    "investigate",
    "rollback",
    "scale_up",
    "restart",
    "enable_circuit_breaker",
    "submit_diagnosis",
]
ServiceHealth = Literal["healthy", "degraded", "down"]
Severity = Literal["info", "warning", "critical"]


class Alert(BaseModel):
    service: str
    severity: Severity
    message: str


class ServiceStatus(BaseModel):
    name: str
    team: str
    status: ServiceHealth
    summary: str
    dependencies: list[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    restoration: float = 0.0
    diagnosis: float = 0.0
    diagnosis_before_fix: float = 0.0
    efficiency: float = 0.0
    wrong_action_penalty: float = 0.0
    total: float = 0.0


class IncidentAction(OpenEnvAction):
    type: ActionType
    service: str | None = None
    cause: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "IncidentAction":
        if self.type in {
            "investigate",
            "rollback",
            "scale_up",
            "restart",
            "enable_circuit_breaker",
            "submit_diagnosis",
        } and not self.service:
            raise ValueError(f"action '{self.type}' requires a service")
        if self.type == "submit_diagnosis" and not self.cause:
            raise ValueError("submit_diagnosis requires a cause")
        return self


class IncidentObservation(OpenEnvObservation):
    difficulty: str
    title: str
    summary: str
    services: list[ServiceStatus]
    alerts: list[Alert]
    recent_logs: dict[str, list[str]]
    action_feedback: str
    valid_actions: list[ActionType]
    investigated_services: list[str] = Field(default_factory=list)
    diagnosed_services: list[str] = Field(default_factory=list)
    resolved_services: list[str] = Field(default_factory=list)
    score_breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)


class IncidentState(OpenEnvState):
    difficulty: str = "uninitialized"
    title: str = "IncidentResponseEnv"
    max_steps: int = 15
    services: list[ServiceStatus] = Field(default_factory=list)
    alerts: list[Alert] = Field(default_factory=list)
    recent_logs: dict[str, list[str]] = Field(default_factory=dict)
    investigated_services: list[str] = Field(default_factory=list)
    diagnosed_services: list[str] = Field(default_factory=list)
    resolved_services: list[str] = Field(default_factory=list)
    failed_actions: int = 0
    success: bool = False
    score_breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
