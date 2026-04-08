from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from .compat import EnvironmentMetadata, OpenEnvEnvironment
from .models import (
    Alert,
    IncidentAction,
    IncidentObservation,
    IncidentState,
    PostMortemIssue,
    PostMortemReport,
    ScoreBreakdown,
    ServiceMetrics,
    ServiceStatus,
)
from .scenarios import (
    IssueDefinition,
    MetricSnapshot,
    ScenarioDefinition,
    get_scenario,
    normalize_text,
)

STATUS_RANK = {"healthy": 0, "degraded": 1, "down": 2}
RANK_TO_STATUS = {value: key for key, value in STATUS_RANK.items()}
VALID_ACTIONS = [
    "investigate",
    "rollback",
    "scale_up",
    "restart",
    "enable_circuit_breaker",
    "submit_diagnosis",
]


@dataclass
class IssueRuntime:
    definition: IssueDefinition
    investigated: bool = False
    diagnosed: bool = False
    diagnosed_before_fix: bool = False
    resolved: bool = False
    investigation_step: int | None = None
    diagnosis_step: int | None = None
    resolution_step: int | None = None


@dataclass
class ActionJournalEntry:
    step: int
    action_type: str
    service: str | None
    cause: str | None
    feedback: str


class IncidentResponseEnvironment(OpenEnvEnvironment):
    SUPPORTS_CONCURRENT_SESSIONS = True

    def __init__(self) -> None:
        self._scenario: ScenarioDefinition | None = None
        self._episode_id: str | None = None
        self._step_count = 0
        self._failed_actions = 0
        self._done = False
        self._circuit_breakers: set[str] = set()
        self._issue_state: dict[str, IssueRuntime] = {}
        self._investigation_notes: dict[str, list[str]] = {}
        self._action_journal: list[ActionJournalEntry] = []
        self._last_feedback = "Environment is ready. Call reset() to start a new incident."

    def reset(
        self,
        seed: Optional[int] = None,
        episode_id: Optional[str] = None,
        **kwargs: object,
    ) -> IncidentObservation:
        del seed
        difficulty = str(kwargs.get("difficulty") or kwargs.get("level") or "easy")
        scenario = get_scenario(difficulty)
        self._scenario = scenario
        self._episode_id = episode_id or f"{scenario.difficulty}-{uuid4().hex[:8]}"
        self._step_count = 0
        self._failed_actions = 0
        self._done = False
        self._circuit_breakers = set()
        self._issue_state = {
            issue.id: IssueRuntime(definition=issue)
            for issue in sorted(scenario.issues, key=lambda item: item.priority)
        }
        self._investigation_notes = {}
        self._action_journal = []
        self._last_feedback = (
            f"Incident initialized for {scenario.title}. "
            f"Use investigate before committing to a diagnosis."
        )
        return self._build_observation(feedback=self._last_feedback, reward=0.0)

    def step(
        self,
        action: IncidentAction,
        timeout_s: Optional[float] = None,
        **kwargs: object,
    ) -> IncidentObservation:
        del timeout_s
        del kwargs
        if self._scenario is None:
            return self._empty_observation("No active incident. Call reset() first.")
        if self._done:
            return self._build_observation(
                feedback="Episode already completed. Call reset() to start a new one.",
                reward=0.0,
            )

        previous_total = self._score_breakdown().total
        self._step_count += 1

        handler = {
            "investigate": self._handle_investigate,
            "submit_diagnosis": self._handle_submit_diagnosis,
            "enable_circuit_breaker": self._handle_circuit_breaker,
            "rollback": self._handle_remediation,
            "scale_up": self._handle_remediation,
            "restart": self._handle_remediation,
        }.get(action.type)

        if handler is None:
            feedback = f"Unsupported action '{action.type}'."
        else:
            feedback = handler(action)

        self._action_journal.append(
            ActionJournalEntry(
                step=self._step_count,
                action_type=action.type,
                service=action.service,
                cause=action.cause,
                feedback=feedback,
            )
        )

        success = self._success()
        if success or self._step_count >= self._scenario.max_steps:
            self._done = True

        new_total = self._score_breakdown().total
        reward = round(new_total - previous_total, 4)
        return self._build_observation(feedback=feedback, reward=reward)

    @property
    def state(self) -> IncidentState:
        if self._scenario is None:
            return IncidentState()
        services, alerts, logs, metrics = self._visible_snapshot()
        score_breakdown = self._score_breakdown()
        return IncidentState(
            episode_id=self._episode_id,
            step_count=self._step_count,
            difficulty=self._scenario.difficulty,
            title=self._scenario.title,
            max_steps=self._scenario.max_steps,
            services=services,
            alerts=alerts,
            recent_logs=logs,
            metrics=metrics,
            investigated_services=self._investigated_services(),
            diagnosed_services=sorted(
                runtime.definition.service
                for runtime in self._issue_state.values()
                if runtime.diagnosed
            ),
            resolved_services=sorted(
                runtime.definition.service
                for runtime in self._issue_state.values()
                if runtime.resolved
            ),
            failed_actions=self._failed_actions,
            success=self._success(),
            score_breakdown=score_breakdown,
            postmortem=self._build_postmortem(score_breakdown),
        )

    def get_metadata(self) -> EnvironmentMetadata:
        return EnvironmentMetadata(
            name="IncidentResponseEnv",
            description=(
                "Deterministic SRE incident-response environment with easy, medium, "
                "hard, severe, and critical triage scenarios."
            ),
            version="0.2.0",
        )

    def _handle_investigate(self, action: IncidentAction) -> str:
        assert self._scenario is not None
        service = action.service or ""
        if service not in self._scenario.services:
            return f"Unknown service '{service}'."

        notes: list[str] = []
        runtime = self._issue_for_service(service)
        if runtime is not None:
            runtime.investigated = True
            if runtime.investigation_step is None:
                runtime.investigation_step = self._step_count
            notes.extend(runtime.definition.investigation_evidence)
        if service in self._scenario.investigation_map:
            notes.extend(self._scenario.investigation_map[service])
        if not notes:
            notes.append(f"{service} looks healthy from the current evidence set.")
        self._investigation_notes[service] = notes
        return " | ".join(notes)

    def _handle_submit_diagnosis(self, action: IncidentAction) -> str:
        service = action.service or ""
        cause = normalize_text(action.cause or "")
        runtime = self._issue_for_service(service)
        if runtime is None:
            return f"No root-cause service named '{service}' exists in this incident."
        if not runtime.investigated:
            return (
                f"Diagnosis for {service} was rejected because the service has not been "
                "investigated yet."
            )
        corroboration = self._corroborating_evidence_count(runtime.definition)
        if corroboration < runtime.definition.corroboration_required:
            needed = runtime.definition.corroboration_required - corroboration
            candidates = ", ".join(runtime.definition.corroborating_services)
            return (
                f"Diagnosis for {service} needs more corroborating evidence from impacted "
                f"services. Investigate {needed} more of: {candidates}."
            )
        if cause not in runtime.definition.normalized_aliases:
            return f"Diagnosis '{action.cause}' does not match the evidence for {service}."
        if runtime.diagnosed:
            return f"Diagnosis for {service} is already on record."

        runtime.diagnosed = True
        runtime.diagnosis_step = self._step_count
        if not runtime.resolved:
            runtime.diagnosed_before_fix = True
        return f"Diagnosis accepted: {service} root cause is {runtime.definition.display_cause}."

    def _handle_circuit_breaker(self, action: IncidentAction) -> str:
        assert self._scenario is not None
        service = action.service or ""
        if service not in self._scenario.services:
            return f"Unknown service '{service}'."

        runtime = self._issue_for_service(service)
        if runtime is not None and runtime.definition.remediation == "enable_circuit_breaker":
            if runtime.resolved:
                return f"{service} is already resolved."
            blocking = self._blocking_services(runtime)
            if blocking:
                return (
                    f"enable_circuit_breaker on {service} did not stick because upstream priorities are "
                    f"still unresolved: {', '.join(blocking)}."
                )
            if service in self._circuit_breakers:
                return f"Circuit breaker for {service} is already enabled."
            self._circuit_breakers.add(service)
            runtime.resolved = True
            runtime.resolution_step = self._step_count
            return runtime.definition.recovery_log

        if not self._service_is_impacted(service):
            return (
                f"Circuit breaker on {service} has no meaningful effect because the "
                "service is not currently suffering upstream dependency failures."
            )
        if service in self._circuit_breakers:
            return f"Circuit breaker for {service} is already enabled."
        self._circuit_breakers.add(service)
        return (
            f"Circuit breaker enabled on {service}. Blast radius is reduced, but the "
            "root cause is still unresolved."
        )

    def _handle_remediation(self, action: IncidentAction) -> str:
        service = action.service or ""
        runtime = self._issue_for_service(service)
        if runtime is None:
            self._failed_actions += 1
            return (
                f"{action.type} on {service} is a wrong-service mutation. "
                "Penalty applied for a destructive action."
            )
        if runtime.resolved:
            return f"{service} is already resolved."
        if action.type != runtime.definition.remediation:
            self._failed_actions += 1
            return (
                f"{action.type} is not the correct remediation for {service}. "
                "Penalty applied."
            )
        blocking = self._blocking_services(runtime)
        if blocking:
            return (
                f"{action.type} on {service} did not stick because upstream priorities are "
                f"still unresolved: {', '.join(blocking)}."
            )

        runtime.resolved = True
        runtime.resolution_step = self._step_count
        return runtime.definition.recovery_log

    def _build_observation(self, feedback: str, reward: float) -> IncidentObservation:
        assert self._scenario is not None
        services, alerts, logs, metrics = self._visible_snapshot()
        score_breakdown = self._score_breakdown()
        self._last_feedback = feedback
        return IncidentObservation(
            difficulty=self._scenario.difficulty,
            title=self._scenario.title,
            summary=self._scenario.summary,
            services=services,
            alerts=alerts,
            recent_logs=logs,
            metrics=metrics,
            action_feedback=feedback,
            valid_actions=VALID_ACTIONS,
            investigated_services=self._investigated_services(),
            diagnosed_services=sorted(
                runtime.definition.service
                for runtime in self._issue_state.values()
                if runtime.diagnosed
            ),
            resolved_services=sorted(
                runtime.definition.service
                for runtime in self._issue_state.values()
                if runtime.resolved
            ),
            score_breakdown=score_breakdown,
            postmortem=self._build_postmortem(score_breakdown),
            reward=reward,
            done=self._done,
            metadata={
                "episode_id": self._episode_id,
                "step_count": self._step_count,
                "success": self._success(),
            },
        )

    def _empty_observation(self, feedback: str) -> IncidentObservation:
        return IncidentObservation(
            difficulty="uninitialized",
            title="IncidentResponseEnv",
            summary="Call reset() with easy, medium, hard, severe, or critical to start an incident.",
            services=[],
            alerts=[],
            recent_logs={},
            metrics={},
            action_feedback=feedback,
            valid_actions=VALID_ACTIONS,
            score_breakdown=ScoreBreakdown(),
            postmortem=None,
            reward=0.0,
            done=False,
            metadata={"episode_id": None, "step_count": 0, "success": False},
        )

    def _visible_snapshot(
        self,
    ) -> tuple[list[ServiceStatus], list[Alert], dict[str, list[str]], dict[str, ServiceMetrics]]:
        assert self._scenario is not None
        service_state = {
            name: {
                "status": "healthy",
                "summary": seed.healthy_summary,
            }
            for name, seed in self._scenario.services.items()
        }
        logs: dict[str, list[str]] = {
            name: [seed.base_log]
            for name, seed in self._scenario.services.items()
        }
        metrics = {
            name: self._snapshot_to_metrics(seed.metrics)
            for name, seed in self._scenario.services.items()
        }
        alerts: list[Alert] = []

        for runtime in self._issue_state.values():
            issue = runtime.definition
            if runtime.investigated:
                logs[issue.service].extend(issue.investigation_evidence)
            if runtime.resolved:
                logs[issue.service].append(issue.recovery_log)
                continue

            for impact in issue.impacts:
                rank = STATUS_RANK[impact.status]
                summary = impact.summary
                alert_severity = impact.alert_severity
                alert_text = impact.alert_text
                log_text = impact.log_text
                impact_metrics = impact.metrics

                if impact.service in self._circuit_breakers and impact.service != issue.service:
                    rank = max(rank - 1, 0)
                    summary = (
                        f"Circuit breaker is containing part of the blast radius from "
                        f"{issue.service}, but the upstream problem remains."
                    )
                    alert_severity = "warning"
                    alert_text = (
                        f"WARNING: circuit breaker active on {impact.service} for upstream "
                        f"failures in {issue.service}"
                    )
                    log_text = (
                        f"{impact.service}: circuit breaker opened while {issue.service} "
                        "remains unhealthy"
                    )
                    impact_metrics = self._blend_metrics(
                        self._scenario.services[impact.service].metrics,
                        impact.metrics,
                        blend_ratio=0.35,
                    )

                if rank > STATUS_RANK[service_state[impact.service]["status"]]:
                    service_state[impact.service]["status"] = RANK_TO_STATUS[rank]
                    service_state[impact.service]["summary"] = summary
                if rank > 0:
                    alerts.append(
                        Alert(
                            service=impact.service,
                            severity=alert_severity,
                            message=alert_text,
                        )
                    )
                    logs[impact.service].append(log_text)
                    metrics[impact.service] = self._merge_metrics(metrics[impact.service], impact_metrics)

        services = [
            ServiceStatus(
                name=seed.name,
                team=seed.team,
                status=service_state[seed.name]["status"],
                summary=service_state[seed.name]["summary"],
                dependencies=list(seed.dependencies),
            )
            for seed in self._scenario.services.values()
        ]
        services.sort(key=lambda item: (STATUS_RANK[item.status], item.name), reverse=True)
        severity_rank = {"info": 0, "warning": 1, "critical": 2}
        alerts.sort(key=lambda item: (severity_rank[item.severity], item.service), reverse=True)
        deduped_logs = {service: self._dedupe(entries)[-4:] for service, entries in logs.items()}
        return services, alerts, deduped_logs, metrics

    def _build_postmortem(self, score_breakdown: ScoreBreakdown) -> PostMortemReport | None:
        if self._scenario is None:
            return None

        root_causes = [
            PostMortemIssue(
                service=runtime.definition.service,
                root_cause=runtime.definition.display_cause,
                remediation=runtime.definition.remediation,
                priority=runtime.definition.priority,
                blast_radius=sorted(
                    {
                        impact.service
                        for impact in runtime.definition.impacts
                        if impact.service != runtime.definition.service
                    }
                ),
                investigated=runtime.investigated,
                diagnosed=runtime.diagnosed,
                resolved=runtime.resolved,
                investigation_step=runtime.investigation_step,
                diagnosis_step=runtime.diagnosis_step,
                resolution_step=runtime.resolution_step,
            )
            for runtime in sorted(self._issue_state.values(), key=lambda item: item.definition.priority)
        ]

        impacted_services = sorted(
            {
                impact.service
                for runtime in self._issue_state.values()
                for impact in runtime.definition.impacts
            }
        )
        if not impacted_services:
            impacted_services = sorted(runtime.definition.service for runtime in self._issue_state.values())

        if self._success():
            status = "resolved"
        elif self._done:
            status = "failed"
        else:
            status = "in_progress"

        resolved_count = sum(1 for runtime in self._issue_state.values() if runtime.resolved)
        total_issues = len(self._issue_state)
        if status == "resolved":
            summary = (
                f"Resolved {total_issues} root cause(s) in {self._step_count} steps with final score "
                f"{score_breakdown.total:.2f}."
            )
        elif status == "failed":
            summary = (
                f"Incident closed without full recovery: {resolved_count}/{total_issues} root cause(s) "
                f"resolved in {self._step_count} steps."
            )
        else:
            summary = (
                f"Incident still in progress after {self._step_count} steps with "
                f"{resolved_count}/{total_issues} root cause(s) resolved."
            )

        timeline = [
            f"step {entry.step}: {self._format_action(entry)} -> {entry.feedback}"
            for entry in self._action_journal
        ]
        if not timeline:
            timeline = ["No actions have been recorded yet."]

        lessons_learned = self._dedupe(self._lessons_learned())
        return PostMortemReport(
            status=status,
            incident_title=self._scenario.title,
            summary=summary,
            total_steps=self._step_count,
            failed_actions=self._failed_actions,
            final_score=score_breakdown.total,
            impacted_services=impacted_services,
            root_causes=root_causes,
            timeline=timeline,
            lessons_learned=lessons_learned,
        )

    def _score_breakdown(self) -> ScoreBreakdown:
        if not self._issue_state:
            return ScoreBreakdown()
        issue_count = len(self._issue_state)
        restoration = 0.40 * (
            sum(1 for runtime in self._issue_state.values() if runtime.resolved) / issue_count
        )
        diagnosis = 0.25 * (
            sum(1 for runtime in self._issue_state.values() if runtime.diagnosed) / issue_count
        )
        diagnosis_before_fix = 0.15 * (
            sum(1 for runtime in self._issue_state.values() if runtime.diagnosed_before_fix) / issue_count
        )
        efficiency = self._efficiency_bonus() if self._success() else 0.0
        penalty = min(self._failed_actions * 0.20, 1.0)
        total = max(
            0.0,
            min(1.0, restoration + diagnosis + diagnosis_before_fix + efficiency - penalty),
        )
        return ScoreBreakdown(
            restoration=round(restoration, 4),
            diagnosis=round(diagnosis, 4),
            diagnosis_before_fix=round(diagnosis_before_fix, 4),
            efficiency=round(efficiency, 4),
            wrong_action_penalty=round(penalty, 4),
            total=round(total, 4),
        )

    def _efficiency_bonus(self) -> float:
        assert self._scenario is not None
        if self._step_count <= 3:
            return 0.10
        remaining = max(self._scenario.max_steps - self._step_count, 0)
        budget = max(self._scenario.max_steps - 3, 1)
        return 0.10 * ((remaining / budget) ** 2)

    def _success(self) -> bool:
        return bool(self._issue_state) and all(
            runtime.resolved and runtime.diagnosed
            for runtime in self._issue_state.values()
        )

    def _issue_for_service(self, service: str) -> IssueRuntime | None:
        for runtime in self._issue_state.values():
            if runtime.definition.service == service:
                return runtime
        return None

    def _blocking_services(self, runtime: IssueRuntime) -> list[str]:
        return [
            self._issue_state[issue_id].definition.service
            for issue_id in runtime.definition.prerequisites
            if not self._issue_state[issue_id].resolved
        ]

    def _service_is_impacted(self, service: str) -> bool:
        for runtime in self._issue_state.values():
            if runtime.resolved:
                continue
            for impact in runtime.definition.impacts:
                if impact.service == service and impact.service != runtime.definition.service:
                    return True
        return False

    def _corroborating_evidence_count(self, issue: IssueDefinition) -> int:
        investigated_services = set(self._investigated_services())
        return sum(
            1 for service in issue.corroborating_services if service in investigated_services
        )

    def _investigated_services(self) -> list[str]:
        investigated_services = {
            runtime.definition.service
            for runtime in self._issue_state.values()
            if runtime.investigated
        }
        investigated_services.update(self._investigation_notes)
        return sorted(investigated_services)

    def _lessons_learned(self) -> list[str]:
        cause_lessons = {
            "out_of_memory": "Add headroom alerts and capacity review before heap saturation reaches the crash threshold.",
            "bad_deploy": "Promote releases only after canary health checks and dependency smoke tests are green.",
            "connection_leak": "Instrument connection pool leak detection and alert before the database exhausts client slots.",
            "cache_memory_pressure": "Review cache capacity planning and hot-key protection before eviction storms form.",
            "bad_model_deploy": "Gate model rollouts with artifact checksum validation and canary scoring before promotion.",
            "dependency_timeout_storm": "Default circuit breakers should trip before a slow dependency saturates worker pools.",
        }
        lessons = [
            cause_lessons.get(runtime.definition.display_cause, "Strengthen runbooks and alerts for this failure mode.")
            for runtime in self._issue_state.values()
        ]
        if self._failed_actions:
            lessons.append("Destructive actions should follow investigation evidence to avoid avoidable penalty.")
        if self._done and not self._success():
            lessons.append("Prioritize the highest-impact root causes sooner to stay within the step budget.")
        return lessons

    @staticmethod
    def _snapshot_to_metrics(snapshot: MetricSnapshot) -> ServiceMetrics:
        return ServiceMetrics(
            cpu_usage=round(snapshot.cpu_usage, 4),
            memory_usage=round(snapshot.memory_usage, 4),
            error_rate=round(snapshot.error_rate, 4),
            p99_latency_ms=snapshot.p99_latency_ms,
            requests_per_minute=snapshot.requests_per_minute,
            deploy_version=snapshot.deploy_version,
        )

    @classmethod
    def _merge_metrics(cls, current: ServiceMetrics, candidate: MetricSnapshot) -> ServiceMetrics:
        dominant_candidate = (
            candidate.error_rate >= current.error_rate
            or candidate.p99_latency_ms >= current.p99_latency_ms
            or candidate.memory_usage >= current.memory_usage
        )
        deploy_version = candidate.deploy_version if dominant_candidate else current.deploy_version
        return ServiceMetrics(
            cpu_usage=round(max(current.cpu_usage, candidate.cpu_usage), 4),
            memory_usage=round(max(current.memory_usage, candidate.memory_usage), 4),
            error_rate=round(max(current.error_rate, candidate.error_rate), 4),
            p99_latency_ms=max(current.p99_latency_ms, candidate.p99_latency_ms),
            requests_per_minute=min(current.requests_per_minute, candidate.requests_per_minute),
            deploy_version=deploy_version,
        )

    @staticmethod
    def _blend_metrics(
        baseline: MetricSnapshot,
        impacted: MetricSnapshot,
        blend_ratio: float,
    ) -> MetricSnapshot:
        inverse = 1.0 - blend_ratio
        return MetricSnapshot(
            cpu_usage=round((baseline.cpu_usage * inverse) + (impacted.cpu_usage * blend_ratio), 4),
            memory_usage=round((baseline.memory_usage * inverse) + (impacted.memory_usage * blend_ratio), 4),
            error_rate=round((baseline.error_rate * inverse) + (impacted.error_rate * blend_ratio), 4),
            p99_latency_ms=int((baseline.p99_latency_ms * inverse) + (impacted.p99_latency_ms * blend_ratio)),
            requests_per_minute=int(
                (baseline.requests_per_minute * inverse) + (impacted.requests_per_minute * blend_ratio)
            ),
            deploy_version=impacted.deploy_version,
        )

    @staticmethod
    def _format_action(entry: ActionJournalEntry) -> str:
        if entry.action_type == "submit_diagnosis":
            return f"submit_diagnosis({entry.service}, {entry.cause})"
        if entry.service:
            return f"{entry.action_type}({entry.service})"
        return entry.action_type

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            output.append(item)
        return output
