from __future__ import annotations

import re
from dataclasses import dataclass, field


def normalize_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip("_")


@dataclass(frozen=True)
class MetricSnapshot:
    cpu_usage: float
    memory_usage: float
    error_rate: float
    p99_latency_ms: int
    requests_per_minute: int
    deploy_version: str


def metric(
    cpu_usage: float,
    memory_usage: float,
    error_rate: float,
    p99_latency_ms: int,
    requests_per_minute: int,
    deploy_version: str,
) -> MetricSnapshot:
    return MetricSnapshot(
        cpu_usage=cpu_usage,
        memory_usage=memory_usage,
        error_rate=error_rate,
        p99_latency_ms=p99_latency_ms,
        requests_per_minute=requests_per_minute,
        deploy_version=deploy_version,
    )


@dataclass(frozen=True)
class ServiceSeed:
    name: str
    team: str
    dependencies: tuple[str, ...]
    healthy_summary: str
    base_log: str
    metrics: MetricSnapshot


@dataclass(frozen=True)
class ServiceImpact:
    service: str
    status: str
    summary: str
    alert_severity: str
    alert_text: str
    log_text: str
    metrics: MetricSnapshot


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
            metrics=metric(0.42, 0.58, 0.01, 180, 12800, "2026.03.28"),
        ),
        "auth-service": ServiceSeed(
            name="auth-service",
            team="identity",
            dependencies=(),
            healthy_summary="Auth token validation latency is normal.",
            base_log="auth-service: login traffic stable with no recent deploys.",
            metrics=metric(0.31, 0.36, 0.01, 140, 7600, "2026.03.30"),
        ),
        "orders-db": ServiceSeed(
            name="orders-db",
            team="data",
            dependencies=(),
            healthy_summary="orders-db is accepting reads and writes normally.",
            base_log="orders-db: replication lag under threshold.",
            metrics=metric(0.28, 0.41, 0.0, 35, 5400, "14.6.0"),
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
                    metrics=metric(0.97, 0.99, 0.84, 4200, 900, "2026.03.28"),
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
            metrics=metric(0.39, 0.47, 0.01, 190, 15000, "2026.04.01"),
        ),
        "auth-service": ServiceSeed(
            name="auth-service",
            team="identity",
            dependencies=(),
            healthy_summary="Auth-service is validating tokens successfully.",
            base_log="auth-service: previous stable release was 2026.03.30.",
            metrics=metric(0.43, 0.45, 0.01, 155, 8300, "2026.03.30"),
        ),
        "profile-service": ServiceSeed(
            name="profile-service",
            team="core-apps",
            dependencies=("auth-service",),
            healthy_summary="profile-service is serving reads normally.",
            base_log="profile-service: cache warm and healthy.",
            metrics=metric(0.36, 0.4, 0.01, 165, 6400, "2026.03.25"),
        ),
        "session-service": ServiceSeed(
            name="session-service",
            team="platform",
            dependencies=("auth-service",),
            healthy_summary="session-service refresh traffic is stable.",
            base_log="session-service: steady hit-rate before incident.",
            metrics=metric(0.34, 0.38, 0.01, 150, 7200, "2026.03.27"),
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
                    metrics=metric(0.92, 0.81, 0.87, 3900, 600, "2026.04.02-rc1"),
                ),
                ServiceImpact(
                    service="api-gateway",
                    status="degraded",
                    summary="api-gateway is waiting on auth-service and timing out.",
                    alert_severity="warning",
                    alert_text="WARNING: api-gateway upstream auth timeout",
                    log_text="api-gateway: upstream auth-service timed out after 1500ms",
                    metrics=metric(0.78, 0.62, 0.29, 2100, 9100, "2026.04.01"),
                ),
                ServiceImpact(
                    service="profile-service",
                    status="degraded",
                    summary="profile-service requests are backing up behind auth-service calls.",
                    alert_severity="warning",
                    alert_text="WARNING: profile-service login dependency degraded",
                    log_text="profile-service: blocked while waiting on auth token introspection",
                    metrics=metric(0.67, 0.59, 0.21, 1700, 3800, "2026.03.25"),
                ),
                ServiceImpact(
                    service="session-service",
                    status="degraded",
                    summary="session-service is retrying auth-dependent refreshes.",
                    alert_severity="warning",
                    alert_text="WARNING: session-service retry storm triggered by auth failures",
                    log_text="session-service: refresh retries spiking due to auth-service failures",
                    metrics=metric(0.74, 0.53, 0.24, 1950, 4100, "2026.03.27"),
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
            metrics=metric(0.43, 0.49, 0.01, 180, 18200, "2026.03.31"),
        ),
        "db-primary": ServiceSeed(
            name="db-primary",
            team="data",
            dependencies=(),
            healthy_summary="db-primary connection pool usage is normal.",
            base_log="db-primary: connection counts flat before the incident.",
            metrics=metric(0.36, 0.44, 0.0, 28, 6200, "14.6.0"),
        ),
        "cache-cluster": ServiceSeed(
            name="cache-cluster",
            team="platform",
            dependencies=("db-primary",),
            healthy_summary="cache-cluster memory pressure is low.",
            base_log="cache-cluster: eviction rate near zero before the incident.",
            metrics=metric(0.33, 0.51, 0.0, 22, 11800, "redis-7.2.1"),
        ),
        "ranking-ml": ServiceSeed(
            name="ranking-ml",
            team="ml-platform",
            dependencies=("feature-store", "cache-cluster"),
            healthy_summary="ranking-ml model pods are healthy.",
            base_log="ranking-ml: previous model release scored cleanly in canary.",
            metrics=metric(0.47, 0.55, 0.01, 240, 4200, "2026.03.30-model-a"),
        ),
        "feature-store": ServiceSeed(
            name="feature-store",
            team="data",
            dependencies=("db-primary",),
            healthy_summary="feature-store read latency is nominal.",
            base_log="feature-store: recent feature lookups are healthy.",
            metrics=metric(0.38, 0.42, 0.0, 58, 7600, "2026.03.26"),
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
                    metrics=metric(0.96, 0.83, 0.92, 5200, 350, "14.6.0"),
                ),
                ServiceImpact(
                    service="feature-store",
                    status="degraded",
                    summary="feature-store is blocked on db-primary.",
                    alert_severity="warning",
                    alert_text="WARNING: feature-store read timeout to db-primary",
                    log_text="feature-store: SELECT timed out waiting for db-primary",
                    metrics=metric(0.73, 0.56, 0.31, 2400, 2200, "2026.03.26"),
                ),
                ServiceImpact(
                    service="api-gateway",
                    status="degraded",
                    summary="api-gateway latency is spiking while db-backed reads fail.",
                    alert_severity="warning",
                    alert_text="WARNING: api-gateway latency spike from db-backed reads",
                    log_text="api-gateway: request latency elevated while db-primary rejects connections",
                    metrics=metric(0.76, 0.61, 0.27, 2100, 9400, "2026.03.31"),
                ),
                ServiceImpact(
                    service="ranking-ml",
                    status="degraded",
                    summary="ranking-ml is slow because feature fetches from db-primary are timing out.",
                    alert_severity="warning",
                    alert_text="WARNING: ranking-ml feature fetches blocked on db-primary",
                    log_text="ranking-ml: feature fetch timed out waiting for feature-store/db-primary",
                    metrics=metric(0.69, 0.62, 0.19, 1900, 2500, "2026.03.30-model-a"),
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
                    metrics=metric(0.82, 0.99, 0.18, 1250, 8400, "redis-7.2.1"),
                ),
                ServiceImpact(
                    service="api-gateway",
                    status="degraded",
                    summary="api-gateway is taking a cache miss storm and overloading backends.",
                    alert_severity="warning",
                    alert_text="WARNING: api-gateway cache miss storm increasing backend load",
                    log_text="api-gateway: cache miss rate jumped from 2% to 41%",
                    metrics=metric(0.79, 0.66, 0.17, 1680, 10100, "2026.03.31"),
                ),
                ServiceImpact(
                    service="ranking-ml",
                    status="degraded",
                    summary="ranking-ml has lost its feature cache and is using cold reads.",
                    alert_severity="warning",
                    alert_text="WARNING: ranking-ml feature cache unavailable",
                    log_text="ranking-ml: feature cache evictions forcing cold fetches",
                    metrics=metric(0.74, 0.7, 0.14, 1420, 3100, "2026.03.30-model-a"),
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
                    metrics=metric(0.89, 0.77, 0.81, 3100, 320, "2026.04.02-b"),
                ),
                ServiceImpact(
                    service="api-gateway",
                    status="degraded",
                    summary="api-gateway is serving stale recommendations because ranking-ml is unavailable.",
                    alert_severity="warning",
                    alert_text="WARNING: api-gateway serving stale recommendations",
                    log_text="api-gateway: recommendation calls returning 503 from ranking-ml",
                    metrics=metric(0.72, 0.58, 0.12, 1380, 9800, "2026.03.31"),
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


SEVERE = ScenarioDefinition(
    difficulty="severe",
    title="Notification Dependency Timeout Storm",
    summary=(
        "notifications-api is saturating worker threads while an external SMTP dependency times out. "
        "The runbook-approved mitigation is to diagnose the timeout storm and enable the circuit breaker "
        "before the queue backlog spreads."
    ),
    max_steps=15,
    services={
        "notifications-api": ServiceSeed(
            name="notifications-api",
            team="engagement",
            dependencies=("smtp-gateway", "user-profile"),
            healthy_summary="notifications-api is dispatching email and push notifications normally.",
            base_log="notifications-api: delivery latency and queue drain were normal before the incident.",
            metrics=metric(0.44, 0.46, 0.01, 180, 9800, "2026.03.29"),
        ),
        "user-events-worker": ServiceSeed(
            name="user-events-worker",
            team="engagement",
            dependencies=("notifications-api",),
            healthy_summary="user-events-worker backlog is low and steady.",
            base_log="user-events-worker: backlog within normal thresholds before paging.",
            metrics=metric(0.32, 0.39, 0.0, 95, 6700, "2026.03.24"),
        ),
        "smtp-gateway": ServiceSeed(
            name="smtp-gateway",
            team="vendor-ops",
            dependencies=(),
            healthy_summary="smtp-gateway handoffs are normally under 400ms.",
            base_log="smtp-gateway: external provider latency stable before the vendor event.",
            metrics=metric(0.27, 0.31, 0.0, 210, 8800, "vendor-edge-2026.03"),
        ),
        "user-profile": ServiceSeed(
            name="user-profile",
            team="core-apps",
            dependencies=(),
            healthy_summary="user-profile reads are healthy.",
            base_log="user-profile: lookup traffic stable with no active alerts.",
            metrics=metric(0.29, 0.35, 0.0, 85, 6100, "2026.03.28"),
        ),
    },
    issues=(
        IssueDefinition(
            id="notifications_timeout_storm",
            service="notifications-api",
            display_cause="dependency_timeout_storm",
            aliases=(
                "dependency_timeout_storm",
                "smtp_timeout_storm",
                "timeout_storm",
                "circuit_breaker_needed",
            ),
            remediation="enable_circuit_breaker",
            priority=1,
            investigation_evidence=(
                "notifications-api thread dumps show most workers blocked on smtp-gateway timeouts.",
                "The incident runbook says to enable the notifications-api circuit breaker to isolate the failing dependency.",
                "The vendor dependency is slow, but the immediate safe remediation is to trip the circuit breaker on notifications-api.",
            ),
            impacts=(
                ServiceImpact(
                    service="notifications-api",
                    status="degraded",
                    summary="notifications-api is saturating on outbound smtp timeouts.",
                    alert_severity="critical",
                    alert_text="CRITICAL: notifications-api outbound smtp timeout storm",
                    log_text="notifications-api: timeout waiting for smtp-gateway after 3000ms",
                    metrics=metric(0.91, 0.74, 0.46, 2850, 3100, "2026.03.29"),
                ),
                ServiceImpact(
                    service="user-events-worker",
                    status="degraded",
                    summary="user-events-worker backlog is rising behind blocked notification publishes.",
                    alert_severity="warning",
                    alert_text="WARNING: user-events-worker backlog growing behind notifications-api",
                    log_text="user-events-worker: publish backlog increasing while notifications-api stalls",
                    metrics=metric(0.71, 0.58, 0.18, 1250, 2500, "2026.03.24"),
                ),
                ServiceImpact(
                    service="smtp-gateway",
                    status="degraded",
                    summary="smtp-gateway latency is spiking at the vendor edge.",
                    alert_severity="warning",
                    alert_text="WARNING: smtp-gateway vendor latency elevated",
                    log_text="smtp-gateway: provider responses now exceed the 2.5s timeout budget",
                    metrics=metric(0.63, 0.47, 0.09, 2600, 4200, "vendor-edge-2026.03"),
                ),
            ),
            recovery_log="notifications-api: circuit breaker enabled and worker saturation is falling while smtp timeouts are isolated.",
            corroborating_services=("user-events-worker",),
            corroboration_required=1,
        ),
    ),
    investigation_map={
        "user-events-worker": (
            "user-events-worker backlog is downstream of notifications-api saturation rather than a queue bug.",
        ),
        "smtp-gateway": (
            "smtp-gateway latency is elevated, but the documented remediation in this environment is to protect notifications-api with a circuit breaker.",
        ),
    },
)


CRITICAL = ScenarioDefinition(
    difficulty="critical",
    title="Search Canary Deployment Regression",
    summary=(
        "A new search-api canary is returning 500s, and the blast radius is visible in the web tier and query cache. "
        "The on-call must gather corroborating evidence, diagnose the release regression, and rollback safely."
    ),
    max_steps=15,
    services={
        "frontend-web": ServiceSeed(
            name="frontend-web",
            team="web",
            dependencies=("search-api",),
            healthy_summary="frontend-web search pages are rendering within SLO.",
            base_log="frontend-web: search page error budget burn is flat before paging.",
            metrics=metric(0.41, 0.45, 0.01, 170, 14200, "2026.03.31"),
        ),
        "search-api": ServiceSeed(
            name="search-api",
            team="discovery",
            dependencies=("query-cache", "catalog-db"),
            healthy_summary="search-api is serving search queries with low error rate.",
            base_log="search-api: previous stable release 2026.03.30 had clean canary metrics.",
            metrics=metric(0.46, 0.49, 0.01, 220, 9100, "2026.03.30"),
        ),
        "query-cache": ServiceSeed(
            name="query-cache",
            team="platform",
            dependencies=(),
            healthy_summary="query-cache hit rate is above 95%.",
            base_log="query-cache: warm hit rate steady before the deployment.",
            metrics=metric(0.3, 0.43, 0.0, 24, 12000, "redis-7.2.1"),
        ),
        "catalog-db": ServiceSeed(
            name="catalog-db",
            team="data",
            dependencies=(),
            healthy_summary="catalog-db read latency is normal.",
            base_log="catalog-db: replicas are healthy with no active failover.",
            metrics=metric(0.27, 0.39, 0.0, 31, 5800, "13.11.2"),
        ),
    },
    issues=(
        IssueDefinition(
            id="search_api_bad_deploy",
            service="search-api",
            display_cause="bad_deploy",
            aliases=(
                "bad_deploy",
                "search_canary_regression",
                "deployment_regression",
                "bad_release",
            ),
            remediation="rollback",
            priority=1,
            investigation_evidence=(
                "The 2026.04.02-search-canary build started five minutes before the alert storm.",
                "Only canary pods in search-api are returning 500s on the query planner path.",
                "The documented mitigation is rolling back search-api to the previous stable release.",
            ),
            impacts=(
                ServiceImpact(
                    service="search-api",
                    status="down",
                    summary="search-api canary pods are failing the query planner path and returning 500s.",
                    alert_severity="critical",
                    alert_text="CRITICAL: search-api canary error rate above 75%",
                    log_text="search-api: canary build 2026.04.02-search-canary panicked in query planner",
                    metrics=metric(0.88, 0.71, 0.79, 3400, 1400, "2026.04.02-search-canary"),
                ),
                ServiceImpact(
                    service="frontend-web",
                    status="degraded",
                    summary="frontend-web search pages are timing out behind search-api failures.",
                    alert_severity="warning",
                    alert_text="WARNING: frontend-web search requests timing out",
                    log_text="frontend-web: search requests exceeded 2s budget waiting on search-api",
                    metrics=metric(0.68, 0.57, 0.22, 1880, 8900, "2026.03.31"),
                ),
                ServiceImpact(
                    service="query-cache",
                    status="degraded",
                    summary="query-cache hit rate is collapsing while failed canaries bypass cache reuse.",
                    alert_severity="warning",
                    alert_text="WARNING: query-cache hit rate cratered after search canary rollout",
                    log_text="query-cache: hit rate dropped from 96% to 58% after search-api canary",
                    metrics=metric(0.66, 0.62, 0.08, 860, 7600, "redis-7.2.1"),
                ),
            ),
            recovery_log="search-api: rollback completed and search traffic is back on the stable release.",
            corroborating_services=("frontend-web", "query-cache"),
            corroboration_required=2,
        ),
    ),
    investigation_map={
        "frontend-web": (
            "frontend-web is failing only on search routes and is otherwise healthy.",
        ),
        "query-cache": (
            "query-cache miss spikes started immediately after the search-api canary was enabled.",
        ),
    },
)


SCENARIOS = {
    EASY.difficulty: EASY,
    MEDIUM.difficulty: MEDIUM,
    HARD.difficulty: HARD,
    SEVERE.difficulty: SEVERE,
    CRITICAL.difficulty: CRITICAL,
}


def get_scenario(difficulty: str) -> ScenarioDefinition:
    key = difficulty.strip().lower()
    if key not in SCENARIOS:
        raise ValueError(
            f"unknown difficulty '{difficulty}'. Expected one of: {', '.join(sorted(SCENARIOS))}"
        )
    return SCENARIOS[key]
