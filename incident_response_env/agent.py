from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Protocol

import requests

from .models import IncidentAction, IncidentObservation

SERVICE_HINTS = [
    "auth-service",
    "db-primary",
    "cache-cluster",
    "ranking-ml",
    "feature-store",
    "api-gateway",
    "profile-service",
    "session-service",
]


class Planner(Protocol):
    def next_action(self, observation: IncidentObservation) -> IncidentAction:
        ...


def infer_cause_and_remediation(text: str) -> tuple[str | None, str | None]:
    lowered = text.lower()
    if "oom" in lowered or "heap" in lowered or "outofmemory" in lowered:
        return "out_of_memory", "scale_up"
    if "too many clients" in lowered or "connection pool" in lowered or "orphaned client" in lowered:
        return "connection_leak", "restart"
    if "maxmemory" in lowered or "evict" in lowered or "memory pressure" in lowered:
        return "cache_memory_pressure", "scale_up"
    if "deploy" in lowered or "rollout" in lowered or "checksum" in lowered or "new build" in lowered:
        if "ranking-ml" in lowered or "model artifact" in lowered:
            return "bad_model_deploy", "rollback"
        return "bad_deploy", "rollback"
    return None, None


class HeuristicPlanner:
    def next_action(self, observation: IncidentObservation) -> IncidentAction:
        if observation.difficulty == "easy":
            return self._next_easy(observation)
        if observation.difficulty == "medium":
            return self._next_medium(observation)
        if observation.difficulty == "hard":
            return self._next_hard(observation)
        return self._next_generic(observation)

    def _next_easy(self, observation: IncidentObservation) -> IncidentAction:
        return self._next_generic(observation)

    def _next_medium(self, observation: IncidentObservation) -> IncidentAction:
        plan = ["api-gateway", "profile-service", "session-service", "auth-service"]
        return self._next_planned_root_cause(
            observation=observation,
            root_service="auth-service",
            diagnosis_cause="bad_deploy",
            remediation="rollback",
            investigation_plan=plan,
        )

    def _next_hard(self, observation: IncidentObservation) -> IncidentAction:
        investigated = set(observation.investigated_services)
        diagnosed = set(observation.diagnosed_services)
        resolved = set(observation.resolved_services)

        phases = [
            {
                "root": "db-primary",
                "cause": "connection_leak",
                "remediation": "restart",
                "investigate": ["api-gateway", "feature-store", "db-primary"],
            },
            {
                "root": "cache-cluster",
                "cause": "cache_memory_pressure",
                "remediation": "scale_up",
                "investigate": ["cache-cluster"],
            },
            {
                "root": "ranking-ml",
                "cause": "bad_model_deploy",
                "remediation": "rollback",
                "investigate": ["ranking-ml"],
            },
        ]

        for phase in phases:
            root = phase["root"]
            if root in diagnosed:
                continue
            for service in phase["investigate"]:
                if service not in investigated:
                    return IncidentAction(type="investigate", service=service)
            if root not in resolved:
                return IncidentAction(type=phase["remediation"], service=root)
            return IncidentAction(
                type="submit_diagnosis",
                service=root,
                cause=phase["cause"],
            )

        return self._next_generic(observation)

    def _next_planned_root_cause(
        self,
        observation: IncidentObservation,
        root_service: str,
        diagnosis_cause: str,
        remediation: str,
        investigation_plan: list[str],
    ) -> IncidentAction:
        investigated = set(observation.investigated_services)
        diagnosed = set(observation.diagnosed_services)
        resolved = set(observation.resolved_services)

        if root_service in diagnosed:
            return self._next_generic(observation)
        for service in investigation_plan:
            if service not in investigated:
                return IncidentAction(type="investigate", service=service)
        if root_service not in resolved:
            return IncidentAction(type=remediation, service=root_service)
        return IncidentAction(
            type="submit_diagnosis",
            service=root_service,
            cause=diagnosis_cause,
        )

    def _next_generic(self, observation: IncidentObservation) -> IncidentAction:
        logs = {
            service: " ".join(entries)
            for service, entries in observation.recent_logs.items()
        }
        investigated = set(observation.investigated_services)
        diagnosed = set(observation.diagnosed_services)
        resolved = set(observation.resolved_services)

        for service in investigated:
            if service in resolved or service not in logs:
                continue
            cause, remediation = infer_cause_and_remediation(logs[service])
            if remediation:
                return IncidentAction(type=remediation, service=service)

        for service in investigated:
            if service in diagnosed or service not in logs:
                continue
            cause, _ = infer_cause_and_remediation(logs[service])
            if cause:
                return IncidentAction(type="submit_diagnosis", service=service, cause=cause)

        hinted_service = self._find_service_hint(observation)
        if hinted_service and hinted_service not in investigated:
            return IncidentAction(type="investigate", service=hinted_service)

        for candidate in self._investigation_order(observation):
            if candidate not in investigated:
                return IncidentAction(type="investigate", service=candidate)

        unresolved = [
            service.name
            for service in observation.services
            if service.status != "healthy" and service.name not in resolved
        ]
        if unresolved:
            return IncidentAction(type="enable_circuit_breaker", service=unresolved[0])

        for service in investigated:
            if service not in diagnosed and service in logs:
                cause, _ = infer_cause_and_remediation(logs[service])
                if cause:
                    return IncidentAction(type="submit_diagnosis", service=service, cause=cause)

        fallback = observation.services[0].name if observation.services else "api-gateway"
        return IncidentAction(type="investigate", service=fallback)

    def _find_service_hint(self, observation: IncidentObservation) -> str | None:
        unhealthy_services = {
            service.name for service in observation.services if service.status != "healthy"
        }
        fragments = [alert.message for alert in observation.alerts]
        for service_name, entries in observation.recent_logs.items():
            if service_name not in unhealthy_services:
                continue
            fragments.extend(entries)
        searchable_text = " ".join(fragments)
        lowered = searchable_text.lower()
        for service in SERVICE_HINTS:
            if service in lowered:
                return service
        return None

    @staticmethod
    def _investigation_order(observation: IncidentObservation) -> list[str]:
        priority = {
            "db-primary": 0,
            "auth-service": 1,
            "cache-cluster": 2,
            "ranking-ml": 3,
            "api-gateway": 4,
            "feature-store": 5,
            "profile-service": 6,
            "session-service": 7,
        }
        return [
            service.name
            for service in sorted(
                observation.services,
                key=lambda item: (item.status == "healthy", priority.get(item.name, 99)),
            )
        ]


@dataclass
class OpenAICompatiblePlanner:
    api_base_url: str
    model_name: str
    api_key: str | None = None
    timeout_seconds: int = 45
    max_retries: int = 3

    def next_action(self, observation: IncidentObservation) -> IncidentAction:
        payload = self._chat_payload(observation)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    self._chat_url(),
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 429 and attempt < self.max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                data = response.json()
                message = data["choices"][0]["message"]["content"]
                action = self._parse_action(message, observation)
                return self._coerce_action(action, observation)
            except requests.HTTPError as error:
                last_error = error
                if attempt < self.max_retries - 1 and getattr(error.response, "status_code", None) == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
        if last_error is not None:
            raise last_error
        raise RuntimeError("LLM planner failed without returning an action")

    def _chat_url(self) -> str:
        if self.api_base_url.endswith("/chat/completions"):
            return self.api_base_url
        return self.api_base_url.rstrip("/") + "/chat/completions"

    def _chat_payload(self, observation: IncidentObservation) -> dict[str, object]:
        state = {
            "difficulty": observation.difficulty,
            "title": observation.title,
            "summary": observation.summary,
            "services": [service.model_dump() for service in observation.services],
            "alerts": [alert.model_dump() for alert in observation.alerts],
            "recent_logs": observation.recent_logs,
            "investigated_services": observation.investigated_services,
            "diagnosed_services": observation.diagnosed_services,
            "resolved_services": observation.resolved_services,
            "last_feedback": observation.action_feedback,
        }
        system_prompt = (
            "You are the on-call SRE agent for IncidentResponseEnv. "
            "Return exactly one JSON object with keys type, service, cause, and notes. "
            "Only include cause for submit_diagnosis. Use evidence before diagnosis."
        )
        user_prompt = (
            "Choose the next best action.\n"
            "Allowed actions: investigate, rollback, scale_up, restart, "
            "enable_circuit_breaker, submit_diagnosis.\n"
            "Visible state:\n"
            f"{json.dumps(state, indent=2)}"
        )
        return {
            "model": self.model_name,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

    def _parse_action(self, raw_text: str, observation: IncidentObservation) -> IncidentAction:
        match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
        if not match:
            return self._fallback_action(raw_text, observation)
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return self._fallback_action(raw_text, observation)
        if "action" in payload and isinstance(payload["action"], dict):
            payload = payload["action"]
        payload = self._normalize_payload(payload)
        try:
            return IncidentAction.model_validate(payload)
        except Exception:
            return self._fallback_action(raw_text, observation)

    def _fallback_action(
        self,
        raw_text: str,
        observation: IncidentObservation,
    ) -> IncidentAction:
        lowered = raw_text.lower()
        action_type = next((action for action in [
            "enable_circuit_breaker",
            "submit_diagnosis",
            "scale_up",
            "rollback",
            "restart",
            "investigate",
        ] if action in lowered), "investigate")
        service = next((service for service in SERVICE_HINTS if service in lowered), None)
        if not service:
            unhealthy = [
                service_state.name
                for service_state in observation.services
                if service_state.status != "healthy"
            ]
            if unhealthy:
                service = unhealthy[0]
            elif observation.services:
                service = observation.services[0].name
            else:
                service = "api-gateway"
        payload: dict[str, object] = {"type": action_type, "service": service}
        if action_type == "submit_diagnosis":
            payload["cause"] = self._infer_cause(raw_text, observation)
        return IncidentAction.model_validate(payload)

    def _infer_cause(self, raw_text: str, observation: IncidentObservation) -> str:
        lowered = raw_text.lower()
        for cause in [
            "bad_model_deploy",
            "cache_memory_pressure",
            "connection_leak",
            "out_of_memory",
            "bad_deploy",
            "oom",
        ]:
            if cause in lowered:
                return "out_of_memory" if cause == "oom" else cause

        logs = " ".join(" ".join(entries) for entries in observation.recent_logs.values()).lower()
        if "oom" in logs or "heap" in logs:
            return "out_of_memory"
        if "checksum" in logs or "artifact" in logs:
            return "bad_model_deploy"
        if "deploy" in logs or "rollout" in logs:
            return "bad_deploy"
        if "too many clients" in logs or "connection pool" in logs:
            return "connection_leak"
        if "maxmemory" in logs or "evict" in logs:
            return "cache_memory_pressure"
        return "bad_deploy"

    @staticmethod
    def _normalize_payload(payload: dict[str, object]) -> dict[str, object]:
        normalized = dict(payload)
        for key in ("service", "cause", "notes"):
            value = normalized.get(key)
            if isinstance(value, str) and value.strip().lower() == "null":
                normalized[key] = None
        return normalized

    @staticmethod
    def _coerce_action(
        action: IncidentAction,
        observation: IncidentObservation,
    ) -> IncidentAction:
        fallback = HeuristicPlanner().next_action(observation)
        available_services = {service.name for service in observation.services}
        investigated = set(observation.investigated_services)
        resolved = set(observation.resolved_services)

        if action.service and available_services and action.service not in available_services:
            return fallback
        if action.service and action.service in resolved and action.type != "submit_diagnosis":
            return fallback
        if action.type == "investigate" and action.service in investigated:
            return fallback
        if action.type in {"rollback", "scale_up", "restart"} and (
            action.service not in investigated
            or fallback.type != action.type
            or fallback.service != action.service
        ):
            return fallback
        if action.type == "submit_diagnosis":
            if action.service not in investigated or fallback.type != "submit_diagnosis":
                return fallback
            return fallback
        return action


def build_planner(mode: str) -> Planner:
    mode = mode.lower()
    if mode == "heuristic":
        return HeuristicPlanner()
    if mode == "llm":
        return _llm_planner_from_env()
    if mode == "auto":
        try:
            return _llm_planner_from_env()
        except RuntimeError:
            return HeuristicPlanner()
    raise ValueError("planner mode must be one of: auto, heuristic, llm")


def _llm_planner_from_env() -> OpenAICompatiblePlanner:
    api_base_url = os.getenv("API_BASE_URL")
    model_name = os.getenv("MODEL_NAME")
    api_key = os.getenv("HF_TOKEN") or os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY")
    if not api_base_url or not model_name:
        raise RuntimeError("API_BASE_URL and MODEL_NAME must be set for llm planner mode")
    return OpenAICompatiblePlanner(
        api_base_url=api_base_url,
        model_name=model_name,
        api_key=api_key,
    )
