from fastapi.testclient import TestClient

from incident_response_env.agent import OpenAICompatiblePlanner
from incident_response_env import build_planner
from incident_response_env.environment import IncidentResponseEnvironment
from incident_response_env.models import IncidentAction
from incident_response_env.scenarios import SCENARIOS
from incident_response_env.server.app import app


def test_easy_perfect_trajectory_scores_ninety_basis_points() -> None:
    env = IncidentResponseEnvironment()
    env.reset(difficulty="easy")
    env.step(IncidentAction(type="investigate", service="api-gateway"))
    env.step(
        IncidentAction(
            type="submit_diagnosis",
            service="api-gateway",
            cause="oom",
        )
    )
    observation = env.step(IncidentAction(type="scale_up", service="api-gateway"))

    assert observation.done is True
    assert observation.score_breakdown.total == 0.9
    assert observation.score_breakdown.diagnosis_before_fix == 0.15
    assert observation.postmortem is not None
    assert observation.postmortem.status == "resolved"
    assert observation.postmortem.root_causes[0].service == "api-gateway"


def test_diagnosis_without_investigation_gets_no_credit() -> None:
    env = IncidentResponseEnvironment()
    env.reset(difficulty="medium")
    observation = env.step(
        IncidentAction(
            type="submit_diagnosis",
            service="auth-service",
            cause="bad_deploy",
        )
    )

    assert "has not been investigated" in observation.action_feedback
    assert observation.score_breakdown.diagnosis == 0.0


def test_hard_requires_db_before_cache() -> None:
    env = IncidentResponseEnvironment()
    env.reset(difficulty="hard")
    env.step(IncidentAction(type="investigate", service="cache-cluster"))
    observation = env.step(IncidentAction(type="scale_up", service="cache-cluster"))

    assert "upstream priorities are still unresolved" in observation.action_feedback
    state = env.state
    assert "cache-cluster" not in state.resolved_services


def test_medium_diagnosis_requires_corroborating_evidence() -> None:
    env = IncidentResponseEnvironment()
    env.reset(difficulty="medium")
    env.step(IncidentAction(type="investigate", service="auth-service"))
    observation = env.step(
        IncidentAction(
            type="submit_diagnosis",
            service="auth-service",
            cause="bad_deploy",
        )
    )

    assert "corroborating evidence" in observation.action_feedback
    assert observation.score_breakdown.diagnosis == 0.0


def test_default_heuristic_scores_descend_with_difficulty() -> None:
    planner = build_planner("heuristic")
    scores: list[float] = []

    for difficulty in ("easy", "medium", "hard"):
        env = IncidentResponseEnvironment()
        observation = env.reset(difficulty=difficulty)
        while not observation.done:
            observation = env.step(planner.next_action(observation))
        scores.append(observation.score_breakdown.total)

    assert scores[0] > scores[1] > scores[2]


def test_metrics_are_exposed_in_observation() -> None:
    env = IncidentResponseEnvironment()
    observation = env.reset(difficulty="easy")

    assert "api-gateway" in observation.metrics
    assert observation.metrics["api-gateway"].memory_usage > 0.9
    assert observation.metrics["api-gateway"].error_rate > 0.5


def test_severe_scenario_uses_circuit_breaker_as_correct_remediation() -> None:
    env = IncidentResponseEnvironment()
    env.reset(difficulty="severe")
    env.step(IncidentAction(type="investigate", service="user-events-worker"))
    env.step(IncidentAction(type="investigate", service="notifications-api"))
    env.step(
        IncidentAction(
            type="submit_diagnosis",
            service="notifications-api",
            cause="dependency_timeout_storm",
        )
    )
    observation = env.step(
        IncidentAction(type="enable_circuit_breaker", service="notifications-api")
    )

    assert observation.done is True
    assert "notifications-api" in observation.resolved_services
    assert observation.postmortem is not None
    assert observation.postmortem.status == "resolved"


def test_all_scenarios_complete_with_bounded_scores() -> None:
    planner = build_planner("heuristic")

    assert len(SCENARIOS) >= 5
    assert {"easy", "medium", "hard", "severe", "critical"} <= set(SCENARIOS)

    for difficulty in SCENARIOS:
        env = IncidentResponseEnvironment()
        observation = env.reset(difficulty=difficulty)
        while not observation.done:
            observation = env.step(planner.next_action(observation))
        assert 0.0 <= observation.score_breakdown.total <= 1.0


def test_llm_parser_normalizes_string_nulls() -> None:
    planner = OpenAICompatiblePlanner(api_base_url="https://example.com", model_name="demo")
    env = IncidentResponseEnvironment()
    observation = env.reset(difficulty="easy")

    action = planner._parse_action(  # type: ignore[attr-defined]
        '{"type":"investigate","service":"api-gateway","cause":"null","notes":"null"}',
        observation,
    )

    assert action.type == "investigate"
    assert action.service == "api-gateway"
    assert action.cause is None


def test_llm_parser_falls_back_from_prose_response() -> None:
    planner = OpenAICompatiblePlanner(api_base_url="https://example.com", model_name="demo")
    env = IncidentResponseEnvironment()
    observation = env.reset(difficulty="easy")

    action = planner._parse_action(  # type: ignore[attr-defined]
        "The next best action is investigate api-gateway to confirm the OOM issue.",
        observation,
    )

    assert action.type == "investigate"
    assert action.service == "api-gateway"


def test_llm_guardrail_replaces_redundant_investigation() -> None:
    planner = OpenAICompatiblePlanner(api_base_url="https://example.com", model_name="demo")
    env = IncidentResponseEnvironment()
    env.reset(difficulty="easy")
    observation = env.step(IncidentAction(type="investigate", service="api-gateway"))

    action = planner._coerce_action(  # type: ignore[attr-defined]
        IncidentAction(type="investigate", service="api-gateway"),
        observation,
    )

    assert action.type == "submit_diagnosis"
    assert action.service == "api-gateway"


def test_http_app_preserves_episode_state_between_requests() -> None:
    client = TestClient(app)
    episode_id = "http-test-episode"

    reset_response = client.post(
        "/reset",
        json={"difficulty": "easy", "episode_id": episode_id},
    )
    assert reset_response.status_code == 200
    reset_payload = reset_response.json()
    assert reset_payload["metadata"]["episode_id"] == episode_id

    step_response = client.post(
        "/step",
        json={
            "episode_id": episode_id,
            "action": {"type": "investigate", "service": "api-gateway"},
        },
    )
    assert step_response.status_code == 200
    step_payload = step_response.json()

    assert "OOMKilled" in step_payload["observation"]["action_feedback"]
    assert step_payload["metadata"]["step_count"] == 1


def test_http_postmortem_endpoint_returns_report() -> None:
    client = TestClient(app)
    episode_id = "postmortem-http-episode"

    client.post("/reset", json={"difficulty": "easy", "episode_id": episode_id})
    client.post(
        "/step",
        json={
            "episode_id": episode_id,
            "action": {"type": "investigate", "service": "api-gateway"},
        },
    )

    response = client.get("/postmortem", params={"episode_id": episode_id})
    payload = response.json()

    assert response.status_code == 200
    assert payload["postmortem"]["status"] == "in_progress"
    assert payload["postmortem"]["incident_title"] == "Single-Service Memory Exhaustion"
