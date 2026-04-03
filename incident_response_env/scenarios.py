from __future__ import annotations

import re
from dataclasses import dataclass, field


def normalize_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip("_")


@dataclass(frozen=True)
class ServiceSeed:
    name: str
    team: str
    dependencies: tuple[str, ...]
    healthy_summary: str
    base_log: str


@dataclass(frozen=True)
class ServiceImpact:
    service: str
    status: str
    summary: str
    alert_severity: str
    alert_text: str
    log_text: str


@dataclass(frozen=True)
class IssueDefinition:
    id: str
    service: str
    display_cause: str
    aliases: tuple[str, ...]
    remediation: str
    priority: int
    investigation_evidence: tuple[str, ...]
    impacts: tuple[ServiceImpact, ...]
    recovery_log: str
    prerequisites: tuple[str, ...] = ()
    corroborating_services: tuple[str, ...] = ()
    corroboration_required: int = 0

    @property
    def normalized_aliases(self) -> set[str]:
        return {normalize_text(alias) for alias in self.aliases}


@dataclass(frozen=True)
class ScenarioDefinition:
    difficulty: str
    title: str
    summary: str
    max_steps: int
    services: dict[str, ServiceSeed]
    issues: tuple[IssueDefinition, ...]
    investigation_map: dict[str, tuple[str, ...]] = field(default_factory=dict)


EASY = ScenarioDefinition(
    difficulty="easy",
    title="Single-Service Memory Exhaustion",
    summary=(
        "api-gateway is crashlooping. The on-call must gather evidence, recover the "
        "service, and submit the root-cause diagnosis."
    ),
    max_steps=15,
    services={
        "api-gateway": ServiceSeed(
            name="api-gateway",
            team="edge",
            dependencies=("auth-service", "orders-db"),
            healthy_summary="Gateway is serving requests within latency SLOs.",
            base_log="api-gateway: steady 200 responses before the incident window.",
        ),
        "auth-service": ServiceSeed(
            name="auth-service",
            team="identity",
            dependencies=(),
            healthy_summary="Auth token validation latency is normal.",
            base_log="auth-service: login traffic stable with no recent deploys.",
        ),
        "orders-db": ServiceSeed(
            name="orders-db",
            team="data",
            dependencies=(),
            healthy_summary="orders-db is accepting reads and writes normally.",
            base_log="orders-db: replication lag under threshold.",
        ),
    },
    issues=(
        IssueDefinition(
            id="api_gateway_oom",
            service="api-gateway",
            display_cause="out_of_memory",
            aliases=(
                "out_of_memory",
                "oom",
                "memory_exhaustion",
                "java_heap_oom",
                "heap_exhaustion",
            ),
            remediation="scale_up",
            priority=1,
            investigation_evidence=(
                "kubectl describe shows the newest api-gateway pod terminated with OOMKilled.",
                "Heap usage hit 98% immediately before the crash.",
                "No upstream dependency errors appear in this service's traces.",
            ),
            impacts=(
                ServiceImpact(
                    service="api-gateway",
                    status="down",
                    summary="api-gateway is crashlooping after exhausting memory.",
                    alert_severity="critical",
                    alert_text="CRITICAL: api-gateway pod crashloop due to OOMKilled",
                    log_text="api-gateway: java.lang.OutOfMemoryError: Java heap space",
                ),
            ),
            recovery_log="api-gateway: memory limit increased and new pods are passing readiness checks.",
        ),
    ),
    investigation_map={
        "auth-service": (
            "auth-service is healthy and not contributing to the current incident.",
        ),
        "orders-db": (
            "orders-db remains healthy; the failure is isolated closer to the edge tier.",
        ),
    },
)


MEDIUM = ScenarioDefinition(
    difficulty="medium",
    title="Cascading Auth Deployment Regression",
    summary=(
        "A bad auth-service rollout is causing timeouts across dependent services. "
        "The on-call must trace the cascade back to auth-service and rollback safely."
    ),
    max_steps=15,
    services={
        "api-gateway": ServiceSeed(
            name="api-gateway",
            team="edge",
            dependencies=("auth-service", "session-service"),
            healthy_summary="Gateway is routing login and API traffic normally.",
            base_log="api-gateway: request volume is nominal.",
        ),
        "auth-service": ServiceSeed(
            name="auth-service",
            team="identity",
            dependencies=(),
            healthy_summary="Auth-service is validating tokens successfully.",
            base_log="auth-service: previous stable release was 2026.03.30.",
        ),
        "profile-service": ServiceSeed(
            name="profile-service",
            team="core-apps",
            dependencies=("auth-service",),
            healthy_summary="profile-service is serving reads normally.",
            base_log="profile-service: cache warm and healthy.",
        ),
        "session-service": ServiceSeed(
            name="session-service",
            team="platform",
            dependencies=("auth-service",),
            healthy_summary="session-service refresh traffic is stable.",
            base_log="session-service: steady hit-rate before incident.",
        ),
    },
    issues=(
        IssueDefinition(
            id="auth_service_bad_deploy",
            service="auth-service",
            display_cause="bad_deploy",
            aliases=(
                "bad_deploy",
                "deployment_regression",
                "bad_release",
                "auth_release_regression",
            ),
            remediation="rollback",
            priority=1,
            investigation_evidence=(
                "Deployment version 2026.04.02-rc1 started five minutes before the page.",
                "Token signature verification fails only on the new auth-service build.",
                "Downstream services are timing out while waiting on auth-service.",
            ),
            impacts=(
                ServiceImpact(
                    service="auth-service",
                    status="down",
                    summary="auth-service is returning 500s after the latest rollout.",
                    alert_severity="critical",
                    alert_text="CRITICAL: auth-service error rate above 80% after rollout",
                    log_text="auth-service: release 2026.04.02-rc1 returning 500 on /token/validate",
                ),
                ServiceImpact(
                    service="api-gateway",
                    status="degraded",
                    summary="api-gateway is waiting on auth-service and timing out.",
                    alert_severity="warning",
                    alert_text="WARNING: api-gateway upstream auth timeout",
                    log_text="api-gateway: upstream auth-service timed out after 1500ms",
                ),
                ServiceImpact(
                    service="profile-service",
                    status="degraded",
                    summary="profile-service requests are backing up behind auth-service calls.",
                    alert_severity="warning",
                    alert_text="WARNING: profile-service login dependency degraded",
                    log_text="profile-service: blocked while waiting on auth token introspection",
                ),
                ServiceImpact(
                    service="session-service",
                    status="degraded",
                    summary="session-service is retrying auth-dependent refreshes.",
                    alert_severity="warning",
                    alert_text="WARNING: session-service retry storm triggered by auth failures",
                    log_text="session-service: refresh retries spiking due to auth-service failures",
                ),
            ),
            recovery_log="auth-service: rollback completed to 2026.03.30 and downstream timeouts are clearing.",
            corroborating_services=("api-gateway", "profile-service", "session-service"),
            corroboration_required=3,
        ),
    ),
    investigation_map={
        "api-gateway": (
            "api-gateway traces point to upstream auth-service timeouts rather than a local crash.",
            "The gateway itself is healthy enough to serve traffic once auth-service recovers.",
        ),
        "profile-service": (
            "profile-service thread dumps show blocked auth token introspection calls.",
        ),
        "session-service": (
            "session-service logs show retries piling up after auth-service started failing.",
        ),
    },
)


HARD = ScenarioDefinition(
    difficulty="hard",
    title="Multi-Incident Priority Triage",
    summary=(
        "db-primary, cache-cluster, and ranking-ml are failing for different reasons at the "
        "same time. Symptoms overlap, and remediation only sticks when issues are fixed in "
        "the right order: db first, then cache, then ranking-ml."
    ),
    max_steps=15,
    services={
        "api-gateway": ServiceSeed(
            name="api-gateway",
            team="edge",
            dependencies=("db-primary", "cache-cluster", "ranking-ml"),
            healthy_summary="api-gateway is serving personalized traffic within SLO.",
            base_log="api-gateway: cache hit rate and recommendation latency were healthy before paging.",
        ),
        "db-primary": ServiceSeed(
            name="db-primary",
            team="data",
            dependencies=(),
            healthy_summary="db-primary connection pool usage is normal.",
            base_log="db-primary: connection counts flat before the incident.",
        ),
        "cache-cluster": ServiceSeed(
            name="cache-cluster",
            team="platform",
            dependencies=("db-primary",),
            healthy_summary="cache-cluster memory pressure is low.",
            base_log="cache-cluster: eviction rate near zero before the incident.",
        ),
        "ranking-ml": ServiceSeed(
            name="ranking-ml",
            team="ml-platform",
            dependencies=("feature-store", "cache-cluster"),
            healthy_summary="ranking-ml model pods are healthy.",
            base_log="ranking-ml: previous model release scored cleanly in canary.",
        ),
        "feature-store": ServiceSeed(
            name="feature-store",
            team="data",
            dependencies=("db-primary",),
            healthy_summary="feature-store read latency is nominal.",
            base_log="feature-store: recent feature lookups are healthy.",
        ),
    },
    issues=(
        IssueDefinition(
            id="db_primary_connection_leak",
            service="db-primary",
            display_cause="connection_leak",
            aliases=(
                "connection_leak",
                "db_connection_leak",
                "connection_pool_exhausted",
                "too_many_clients",
                "pool_exhaustion",
            ),
            remediation="restart",
            priority=1,
            investigation_evidence=(
                "db-primary has hundreds of orphaned client sessions and its pool is exhausted.",
                "feature-store requests are hanging while waiting on fresh db-primary connections.",
                "The runbook-approved remediation is restarting db-primary to clear the leak.",
            ),
            impacts=(
                ServiceImpact(
                    service="db-primary",
                    status="down",
                    summary="db-primary is refusing new connections after a connection leak.",
                    alert_severity="critical",
                    alert_text="CRITICAL: db-primary connection pool exhausted",
                    log_text="db-primary: FATAL: sorry, too many clients already",
                ),
                ServiceImpact(
                    service="feature-store",
                    status="degraded",
                    summary="feature-store is blocked on db-primary.",
                    alert_severity="warning",
                    alert_text="WARNING: feature-store read timeout to db-primary",
                    log_text="feature-store: SELECT timed out waiting for db-primary",
                ),
                ServiceImpact(
                    service="api-gateway",
                    status="degraded",
                    summary="api-gateway latency is spiking while db-backed reads fail.",
                    alert_severity="warning",
                    alert_text="WARNING: api-gateway latency spike from db-backed reads",
                    log_text="api-gateway: request latency elevated while db-primary rejects connections",
                ),
                ServiceImpact(
                    service="ranking-ml",
                    status="degraded",
                    summary="ranking-ml is slow because feature fetches from db-primary are timing out.",
                    alert_severity="warning",
                    alert_text="WARNING: ranking-ml feature fetches blocked on db-primary",
                    log_text="ranking-ml: feature fetch timed out waiting for feature-store/db-primary",
                ),
            ),
            recovery_log="db-primary: restart completed and connection pool usage has returned to normal.",
            corroborating_services=("feature-store", "api-gateway"),
            corroboration_required=1,
        ),
        IssueDefinition(
            id="cache_cluster_memory",
            service="cache-cluster",
            display_cause="cache_memory_pressure",
            aliases=(
                "cache_memory_pressure",
                "memory_pressure",
                "eviction_storm",
                "redis_memory_pressure",
            ),
            remediation="scale_up",
            priority=2,
            investigation_evidence=(
                "cache-cluster memory is pinned at 99% and hot keys are being evicted.",
                "The cache runbook recommends adding capacity instead of restarting nodes.",
                "Until db-primary is stable, the cache refill storm immediately re-saturates the cluster.",
            ),
            impacts=(
                ServiceImpact(
                    service="cache-cluster",
                    status="degraded",
                    summary="cache-cluster is thrashing under sustained memory pressure.",
                    alert_severity="critical",
                    alert_text="CRITICAL: cache-cluster memory ceiling reached",
                    log_text="cache-cluster: maxmemory reached, evicting hot keys",
                ),
                ServiceImpact(
                    service="api-gateway",
                    status="degraded",
                    summary="api-gateway is taking a cache miss storm and overloading backends.",
                    alert_severity="warning",
                    alert_text="WARNING: api-gateway cache miss storm increasing backend load",
                    log_text="api-gateway: cache miss rate jumped from 2% to 41%",
                ),
                ServiceImpact(
                    service="ranking-ml",
                    status="degraded",
                    summary="ranking-ml has lost its feature cache and is using cold reads.",
                    alert_severity="warning",
                    alert_text="WARNING: ranking-ml feature cache unavailable",
                    log_text="ranking-ml: feature cache evictions forcing cold fetches",
                ),
            ),
            recovery_log="cache-cluster: capacity increased and eviction rate is back under control.",
            prerequisites=("db_primary_connection_leak",),
            corroborating_services=("api-gateway", "ranking-ml"),
            corroboration_required=1,
        ),
        IssueDefinition(
            id="ranking_ml_bad_model",
            service="ranking-ml",
            display_cause="bad_model_deploy",
            aliases=(
                "bad_model_deploy",
                "model_deploy_regression",
                "artifact_checksum_failure",
                "bad_deploy",
            ),
            remediation="rollback",
            priority=3,
            investigation_evidence=(
                "ranking-ml canary build 2026.04.02-b introduced a model artifact checksum mismatch.",
                "Only the new ranking-ml pods are crashlooping after the latest rollout.",
                "Rollback is the documented mitigation once upstream dependencies are healthy.",
            ),
            impacts=(
                ServiceImpact(
                    service="ranking-ml",
                    status="down",
                    summary="ranking-ml canary pods are crashlooping after a bad model rollout.",
                    alert_severity="critical",
                    alert_text="CRITICAL: ranking-ml canary crashloop",
                    log_text="ranking-ml: model artifact checksum mismatch on startup",
                ),
                ServiceImpact(
                    service="api-gateway",
                    status="degraded",
                    summary="api-gateway is serving stale recommendations because ranking-ml is unavailable.",
                    alert_severity="warning",
                    alert_text="WARNING: api-gateway serving stale recommendations",
                    log_text="api-gateway: recommendation calls returning 503 from ranking-ml",
                ),
            ),
            recovery_log="ranking-ml: rollback completed and recommendation traffic is back on the stable model.",
            prerequisites=("db_primary_connection_leak", "cache_cluster_memory"),
            corroborating_services=("api-gateway",),
            corroboration_required=1,
        ),
    ),
    investigation_map={
        "api-gateway": (
            "api-gateway sees simultaneous db latency, cache miss spikes, and ranking-ml 503s.",
            "The symptoms overlap, so you need to disambiguate multiple root causes with evidence.",
        ),
        "feature-store": (
            "feature-store stack traces point to db-primary connection starvation.",
        ),
    },
)


SCENARIOS = {
    EASY.difficulty: EASY,
    MEDIUM.difficulty: MEDIUM,
    HARD.difficulty: HARD,
}


def get_scenario(difficulty: str) -> ScenarioDefinition:
    key = difficulty.strip().lower()
    if key not in SCENARIOS:
        raise ValueError(
            f"unknown difficulty '{difficulty}'. Expected one of: {', '.join(sorted(SCENARIOS))}"
        )
    return SCENARIOS[key]
