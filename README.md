---
title: IncidentResponseEnv
emoji: 🚨
colorFrom: red
colorTo: pink
sdk: docker
pinned: false
app_port: 8000
base_path: /
tags:
  - openenv
---

# 🚨 IncidentResponseEnv

**A deterministic OpenEnv benchmark for SRE incident investigation, diagnosis, and remediation under a bounded action budget.**

IncidentResponseEnv evaluates whether an agent can operate like an on-call production engineer: inspect alerts, logs, and service metrics; trace failures through dependencies; submit the correct root-cause diagnosis; and apply the right remediation without taking destructive actions on healthy systems.

This is a real workflow, not a toy abstraction. SRE and platform teams routinely investigate cascading outages, rollout regressions, memory exhaustion, and dependency failures under time pressure. The benchmark focuses that workflow into a judgeable environment with explicit scenarios, fixed remediation mappings, normalized scores, and typed actions.

> **Live Deployment:** [`utkrishtu-incident-response-env.hf.space`](https://utkrishtu-incident-response-env.hf.space)

---

## Benchmark Summary

| Property | Value |
|---|---|
| **Domain** | Production SRE incident response |
| **Task loop** | Investigate -> diagnose -> remediate |
| **Action space** | `investigate`, `submit_diagnosis`, `rollback`, `scale_up`, `restart`, `enable_circuit_breaker` |
| **Task set** | `easy`, `medium`, `hard`, `severe`, `critical` |
| **Scoring** | Deterministic, normalized to `[0.0, 1.0]` |
| **Validation surface** | Typed observations, typed actions, fixed scenarios, tested inference log format |

---

## Why This Task Matters

| Criterion | How This Environment Satisfies It |
|---|---|
| **Real-world utility** | Mirrors production on-call triage that software companies perform daily |
| **Sequential reasoning** | Agents must gather evidence before diagnosis and remediation |
| **Operational risk** | Wrong destructive actions incur explicit penalties |
| **Trajectory sensitivity** | Partial credit accumulates over the full repair trajectory |
| **Difficulty progression** | Scenarios scale from one failing service to overlapping multi-service incidents |

---

## Why This Is Easy To Judge

- **Deterministic scenario definitions:** every incident is declared in code with fixed services, alerts, logs, metrics, and expected remediation.
- **Explicit root-cause aliases:** each issue accepts a bounded set of normalized diagnosis aliases rather than free-form grading.
- **Fixed remediation mapping:** every incident definition encodes the correct action for resolution.
- **Bounded action space:** agents can only choose from six typed operations.
- **Normalized score range:** totals are clamped to `[0.0, 1.0]` for straightforward comparison across tasks.
- **Tested inference contract:** the repo validates that `inference.py` emits the required `[START]`, `[STEP]`, and `[END]` flow.

---

## Verified Baseline Results

Current repo-local baseline using the root `inference.py` on the deterministic planner path produces:

| Task | Score | Steps | Success |
|---|---|---|---|
| `easy` | `0.90` | `3` | `true` |
| `medium` | `0.86` | `6` | `true` |
| `hard` | `0.81` | `11` | `true` |
| `severe` | `0.88` | `4` | `true` |
| `critical` | `0.87` | `5` | `true` |

**Mean score across all tasks: `0.86`**

The baseline completes all five tasks successfully while preserving deterministic reward values in the normalized `[0.0, 1.0]` range.

---

## Environment Contract

The environment implements the full **OpenEnv spec**:

| Method | Description |
|---|---|
| `reset(difficulty)` | Starts a fresh incident and returns the initial `IncidentObservation` |
| `step(action)` | Applies one `IncidentAction` and returns the updated observation + incremental reward |
| `state()` | Returns the full `IncidentState` including service health, metrics, score breakdown, and post-mortem |

### HTTP Endpoints (FastAPI)

| Endpoint | Method | Description |
|---|---|---|
| `/health` | `GET` | Returns `{"status": "healthy"}` |
| `/reset` | `POST` | Starts a new episode, returns initial observation |
| `/step` | `POST` | Applies an action, returns observation + reward + done |
| `/state` | `GET` | Returns current environment state |
| `/postmortem` | `GET` | Returns the structured incident post-mortem for an episode |
| `/schema` | `GET` | Returns Action/Observation JSON schemas |

---

## Observation Space

Each observation (`IncidentObservation`) contains:

| Field | Type | Description |
|---|---|---|
| `difficulty` | `str` | Current task level (`easy`, `medium`, `hard`, `severe`, `critical`) |
| `title` | `str` | Human-readable incident title |
| `summary` | `str` | Incident briefing for the on-call agent |
| `services` | `list[ServiceStatus]` | Name, team, status (`healthy`/`degraded`/`down`), summary, dependencies |
| `alerts` | `list[Alert]` | Active alerts with service, severity (`info`/`warning`/`critical`), and message |
| `recent_logs` | `dict[str, list[str]]` | Per-service log entries relevant to the incident |
| `metrics` | `dict[str, ServiceMetrics]` | Per-service operational metrics including CPU, memory, error rate, p99 latency, request volume, and deploy version |
| `investigated_services` | `list[str]` | Services the agent has already investigated |
| `diagnosed_services` | `list[str]` | Services with submitted diagnoses |
| `resolved_services` | `list[str]` | Services successfully remediated |
| `action_feedback` | `str` | Human-readable feedback from the last action |
| `score_breakdown` | `ScoreBreakdown` | Running score: restoration, diagnosis, efficiency, penalties |
| `postmortem` | `PostMortemReport \| null` | Structured incident report with timeline, blast radius, remediation steps, and lessons learned |

### Structured Metrics

Each service exposes deterministic metrics designed to feel like a lightweight production dashboard:

- `cpu_usage`
- `memory_usage`
- `error_rate`
- `p99_latency_ms`
- `requests_per_minute`
- `deploy_version`

These metrics change with incident severity, so the agent can reason from signals beyond raw logs.

---

## Action Space

Agents act with a typed `IncidentAction` (Pydantic model):

| Action | Required Fields | Description |
|---|---|---|
| `investigate` | `service` | Examine a service to gather diagnostic evidence |
| `submit_diagnosis` | `service`, `cause` | Submit a root-cause hypothesis for a service |
| `rollback` | `service` | Roll back the last deployment on a service |
| `scale_up` | `service` | Add capacity to a service |
| `restart` | `service` | Restart a service to clear transient state |
| `enable_circuit_breaker` | `service` | Enable circuit breaker to isolate a failing dependency |

**Example action (JSON):**
```json
{"type": "investigate", "service": "api-gateway"}
{"type": "submit_diagnosis", "service": "auth-service", "cause": "bad_deploy"}
{"type": "rollback", "service": "auth-service"}
```

---

## Task Descriptions

### Easy — Single-Service Memory Exhaustion
> `api-gateway` is crashlooping after exhausting its Java heap.

| Property | Value |
|---|---|
| **Services** | `api-gateway`, `auth-service`, `orders-db` |
| **Root Cause** | `api-gateway` → `out_of_memory` |
| **Correct Remediation** | `scale_up('api-gateway')` |
| **Expected Steps** | 3 |
| **What the agent must do** | Investigate `api-gateway`, observe OOMKilled evidence, submit diagnosis, scale up |

### Medium — Cascading Auth Deployment Regression
> A bad `auth-service` deployment is causing timeouts across 3 dependent services.

| Property | Value |
|---|---|
| **Services** | `api-gateway`, `auth-service`, `profile-service`, `session-service` |
| **Root Cause** | `auth-service` → `bad_deploy` |
| **Correct Remediation** | `rollback('auth-service')` |
| **Expected Steps** | 6–9 |
| **What the agent must do** | Investigate impacted services to gather corroborating evidence (3 required), trace the cascade back to `auth-service`, submit diagnosis, rollback |

### Hard — Multi-Incident Priority Triage
> `db-primary`, `cache-cluster`, and `ranking-ml` are failing for different, overlapping reasons simultaneously.

| Property | Value |
|---|---|
| **Services** | `api-gateway`, `db-primary`, `cache-cluster`, `ranking-ml`, `feature-store` |
| **Root Causes** | `db-primary` → `connection_leak`, `cache-cluster` → `cache_memory_pressure`, `ranking-ml` → `bad_model_deploy` |
| **Correct Remediations** | `restart('db-primary')` → `scale_up('cache-cluster')` → `rollback('ranking-ml')` |
| **Expected Steps** | 10–15 |
| **What the agent must do** | Disambiguate overlapping symptoms, respect dependency-ordered remediation (db first, then cache, then ML), diagnose and fix all three root causes |

### Severe — Notification Dependency Timeout Storm
> `notifications-api` is saturating because an external SMTP dependency is timing out, and the safest mitigation is to isolate the dependency with a circuit breaker.

| Property | Value |
|---|---|
| **Services** | `notifications-api`, `user-events-worker`, `smtp-gateway`, `user-profile` |
| **Root Cause** | `notifications-api` → `dependency_timeout_storm` |
| **Correct Remediation** | `enable_circuit_breaker('notifications-api')` |
| **Expected Steps** | 4–6 |
| **What the agent must do** | Investigate the backlog symptoms, diagnose the timeout storm, and enable the circuit breaker on the root service |

### Critical — Search Canary Deployment Regression
> A broken `search-api` canary is throwing 500s, dragging down the frontend and collapsing query-cache hit rates.

| Property | Value |
|---|---|
| **Services** | `frontend-web`, `search-api`, `query-cache`, `catalog-db` |
| **Root Cause** | `search-api` → `bad_deploy` |
| **Correct Remediation** | `rollback('search-api')` |
| **Expected Steps** | 5–7 |
| **What the agent must do** | Gather corroborating evidence from the web tier and cache, diagnose the canary regression, and rollback safely |

### Structured Post-Mortem

Every observation and state snapshot now carries a typed post-mortem object that summarizes:

- overall incident status (`in_progress`, `resolved`, or `failed`)
- impacted services
- each root cause with investigation, diagnosis, and resolution steps
- a step-by-step timeline
- lessons learned tailored to the failure mode

---

## Reward Function & Scoring

Scores are **deterministic** and clamped to `[0.0, 1.0]`. The reward function provides meaningful signal over the full trajectory, not just binary success/failure.

| Component | Weight | Description |
|---|---|---|
| **Restoration** | `+0.40` | Proportional to services successfully restored to healthy |
| **Diagnosis** | `+0.25` | Correct root-cause diagnosis submitted for each issue |
| **Diagnosis-before-fix bonus** | `+0.15` | Awarded when diagnosis is submitted *before* the remediation action |
| **Efficiency** | `+0.10` | Bonus for solving within the step budget (15 steps) |
| **Wrong action penalty** | `−0.20` | Per incorrect destructive action (rollback/restart/scale on wrong service) |

On multi-root-cause incidents (hard), restoration and diagnosis are proportionally split across issues.

This repo also preserves deterministic grading for the new scenarios: `severe` tests dependency isolation with `enable_circuit_breaker`, and `critical` adds another release-regression incident with corroboration requirements.

---

## Inference Strategy

`inference.py` uses a robust 3-tier decision pipeline:

1. **Primary LLM** (`llama-3.3-70b-versatile` via Groq) — Full conversation history with token-optimized state compression
2. **Backup LLM** (`llama-3.1-8b-instant`) — Automatically triggered on 429 rate limits or 413 context overflows
3. **Heuristic Planner** — Deterministic Python rules that guarantee episode completion even if both LLMs fail

The script iterates over the full scenario catalog, always emits `[START]`, `[STEP]`, and `[END]` lines, and now guarantees `[END]` emission even if an exception happens mid-episode.

### Required Environment Variables

```
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.groq.com/openai/v1")
MODEL_NAME   = os.getenv("MODEL_NAME", "llama-3.3-70b-versatile")
HF_TOKEN     = os.getenv("HF_TOKEN")
```

> **Note:** Defaults are set only for `API_BASE_URL` and `MODEL_NAME`. `HF_TOKEN` has no default and must be provided via environment secrets.

If `HF_TOKEN` is omitted locally, the script still completes by falling back to the built-in deterministic heuristic planner.

---

## Quick Start

### Local Setup
```bash
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\Activate.ps1
pip install -e .
```

### Run Tests
```bash
python3 -m pytest
```

### Run the Inference Baseline
```bash
API_BASE_URL="https://api.groq.com/openai/v1" \
MODEL_NAME="llama-3.3-70b-versatile" \
HF_TOKEN="your-groq-api-key" \
python3 inference.py
```

### Start the HTTP Server
```bash
uvicorn server.app:app --host 0.0.0.0 --port 8000
```

### Validate OpenEnv Spec
```bash
openenv validate
```

---

## Deployment

### Docker
```bash
docker build -t incident-response-env .
docker run -p 8000:8000 \
  -e HF_TOKEN="your-key" \
  -e API_BASE_URL="https://api.groq.com/openai/v1" \
  -e MODEL_NAME="llama-3.3-70b-versatile" \
  incident-response-env
```

### Hugging Face Spaces (Production)

This repo deploys as a **Docker Space** on Hugging Face. Required variables in Space Settings:

| Type | Name | Value |
|---|---|---|
| **Secret** | `HF_TOKEN` | Your Groq API key |
| **Variable** | `API_BASE_URL` | `https://api.groq.com/openai/v1` |
| **Variable** | `MODEL_NAME` | `llama-3.3-70b-versatile` |

---

## Repository Layout

```
├── inference.py                          # Baseline inference script (OpenAI client + structured stdout)
├── openenv.yaml                          # OpenEnv spec metadata
├── Dockerfile                            # Containerized deployment
├── pyproject.toml                        # Dependencies and build config
├── README.md                             # This file
├── server/
│   └── app.py                            # FastAPI entrypoint (re-exports from incident_response_env)
├── incident_response_env/
│   ├── environment.py                    # Core environment engine (reset/step/state)
│   ├── scenarios.py                      # Five incident definitions with deterministic metrics
│   ├── models.py                         # Typed Pydantic models (Action, Observation, State, Metrics, PostMortem)
│   ├── agent.py                          # HeuristicPlanner + LLM-compatible planner
│   ├── compat.py                         # OpenEnv base class compatibility layer
│   └── server/
│       └── app.py                        # FastAPI routes (/health, /reset, /step, /state, /postmortem)
├── tests/
│   └── test_environment.py               # Regression test suite
└── scripts/
    └── live_space_eval.py                # Live deployment verification
```

---

## Team

**Team Data Driven** — OpenEnv Hackathon 2026

- Utkrisht Umang (Team Lead)
- Abhigyan Shekhar
- Saahya KS
