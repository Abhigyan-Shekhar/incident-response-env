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

# IncidentResponseEnv

IncidentResponseEnv is an OpenEnv environment for SRE on-call incident triage. It gives an agent a broken production system, realistic alerts and logs, and a constrained remediation interface. The agent must investigate the incident, identify the real root cause, execute the right fix, and submit a defensible diagnosis.

This environment is built for the OpenEnv hackathon requirement: a real-world task, standard `reset()` / `step()` / `state()` interaction, three difficulty levels, deterministic grading in `[0.0, 1.0]`, and reproducible baseline execution.

Live deployment: [abhi1203-incident-response-env.hf.space](https://abhi1203-incident-response-env.hf.space)

## Why This Task Matters

SRE incident response is a real production workflow at every software company. When a service degrades at 2am, the on-call engineer has to trace symptoms across dependencies, separate root cause from blast radius, apply the correct remediation, and restore service under time pressure. That makes it a strong OpenEnv task:

- it is a real human job, not a toy game
- success depends on sequential reasoning over changing state
- wrong actions carry operational cost
- the environment can grade the full trajectory, not just the final answer

## Environment Contract

The environment exposes the standard OpenEnv loop:

- `reset(difficulty)` starts a fresh incident and returns the initial observation
- `step(action)` applies one action and returns the updated observation plus incremental reward
- `state()` returns the current environment state, service health, and score breakdown

The HTTP server exposes:

- `GET /health`
- `POST /reset`
- `POST /step`
- `GET /state`

The deployed Space keeps per-episode state across requests and returns an `episode_id` in response metadata so multi-step evaluation remains deterministic.

## Observation and Action Model

An observation contains:

- service health across the incident graph
- active alerts
- recent logs per service
- investigated services
- diagnosed services
- resolved services
- score breakdown

Agents act with a typed `IncidentAction`:

- `investigate`
- `rollback`
- `scale_up`
- `restart`
- `enable_circuit_breaker`
- `submit_diagnosis`

`submit_diagnosis` is only rewarded after the root-cause service has been investigated. Medium and hard scenarios also require corroborating evidence from impacted services, so the agent cannot get credit by guessing from a single alert.

## Difficulty Levels

| Level | Scenario | Required behavior |
| --- | --- | --- |
| `easy` | `api-gateway` crashes with out-of-memory pressure | investigate, identify OOM, scale up, submit diagnosis |
| `medium` | bad `auth-service` deployment cascades into downstream timeouts | investigate impacted services, trace to `auth-service`, rollback, submit diagnosis |
| `hard` | `db-primary`, `cache-cluster`, and `ranking-ml` fail for different reasons | disambiguate symptoms, respect remediation order, resolve and diagnose all three |

Difficulty progression is intentional. The hard task requires the agent to distinguish overlapping symptoms, investigate dependencies, and remediate in the correct order.

## Deterministic Scoring

Scores are deterministic and clamped to `[0.0, 1.0]`.

- `+0.40` restoration progress
- `+0.25` correct diagnosis credit
- `+0.15` bonus when diagnosis is submitted before the fix
- `+0.10` efficiency bonus within the 15-step budget
- `-0.20` per wrong destructive action

On multi-root-cause incidents, restoration and diagnosis are apportioned across active issues. That makes reward trajectory-sensitive rather than pure win/loss.

## Verified Baseline Results

The live baseline was run end-to-end against the deployed Hugging Face Space with the Groq OpenAI-compatible endpoint:

| Task | Score | Steps | Success |
| --- | --- | --- | --- |
| `easy` | `0.75` | `3` | `true` |
| `medium` | `0.7063` | `6` | `true` |
| `hard` | `0.6611` | `11` | `true` |

Mean reward across the three tasks: `0.7058`

This gives a clear downward difficulty curve while still demonstrating successful completion on all tasks.

## Quick Start

Create a local environment and run the deterministic baseline:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python3 -m pytest
python3 inference.py --planner heuristic
```

Start the HTTP server locally:

```bash
uvicorn server.app:app --host 0.0.0.0 --port 8000
```

Validate against OpenEnv:

```bash
source .venv/bin/activate
python3 -m openenv.cli validate
```

## LLM Baseline

`inference.py` supports:

- `--planner heuristic` for the deterministic baseline
- `--planner llm` for an OpenAI-compatible endpoint
- `--planner auto` to use the LLM when configured and otherwise fall back to heuristics

Expected environment variables:

- `API_BASE_URL`
- `MODEL_NAME`
- `HF_TOKEN`

Example with Groq:

```bash
API_BASE_URL="https://api.groq.com/openai/v1" \
MODEL_NAME="llama-3.3-70b-versatile" \
HF_TOKEN="$YOUR_GROQ_API_KEY" \
python3 inference.py --planner llm
```

To verify the live deployed environment end to end:

```bash
source .venv/bin/activate
PYTHONPATH=. python3 scripts/live_space_eval.py
```

## Deployment

This repo includes:

- `openenv.yaml`
- `Dockerfile`
- `server/app.py`

The Hugging Face Space is configured as a Docker Space and expects:

- `API_BASE_URL` as a variable
- `MODEL_NAME` as a variable
- `HF_TOKEN` as a secret

## Repository Layout

- `incident_response_env/environment.py`: deterministic incident engine
- `incident_response_env/scenarios.py`: easy, medium, and hard incident definitions
- `incident_response_env/agent.py`: heuristic and OpenAI-compatible planners
- `inference.py`: baseline runner with structured `[START]`, `[STEP]`, and `[END]` logs
- `scripts/live_space_eval.py`: live deployment verifier
- `tests/test_environment.py`: regression coverage
