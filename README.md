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

IncidentResponseEnv is an OpenEnv-style execution environment for SRE on-call incident triage. It simulates realistic production incidents where an agent must investigate symptoms, identify the true root cause, apply the correct remediation, and formally submit a diagnosis before the episode closes.

## What It Simulates

- `easy`: api-gateway crashes from out-of-memory pressure
- `medium`: a bad auth-service deployment cascades into downstream timeouts
- `hard`: db-primary, cache-cluster, and ranking-ml fail for different reasons and must be fixed in priority order

The environment exposes the standard interaction loop:

- `reset(difficulty)` starts a fresh incident
- `step(action)` executes one agent action
- `state()` returns the current environment state, score breakdown, and service health

## Action Space

Agents act with a typed `IncidentAction`:

- `investigate`
- `rollback`
- `scale_up`
- `restart`
- `enable_circuit_breaker`
- `submit_diagnosis`

`submit_diagnosis` only earns credit when the root-cause service has already been investigated. On the medium and hard tasks, the environment also requires corroborating evidence from impacted services so the agent has to actually trace the cascade instead of guessing from a single log line.

## Scoring

Scores are deterministic and clamped to `[0.0, 1.0]`.

- `+0.40` restoration progress across unresolved root causes
- `+0.25` correct diagnosis credit
- `+0.15` diagnosis-before-fix bonus
- `+0.10` efficiency bonus when the incident is closed within the 15-step budget, with sharper decay after the first few actions
- `-0.20` per wrong destructive action on the wrong service or with the wrong remediation

On multi-root-cause incidents, restoration and diagnosis credit are apportioned across the active issues so the reward evolves across the trajectory instead of only at the end.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pytest
python inference.py --planner heuristic
```

Run the HTTP server locally:

```bash
uvicorn server.app:app --host 0.0.0.0 --port 8000
```

## LLM Baseline

`inference.py` supports either:

- `--planner heuristic` for a deterministic local baseline
- `--planner llm` for an OpenAI-compatible endpoint
- `--planner auto` to use the LLM when configured and otherwise fall back to heuristics

LLM mode expects:

- `API_BASE_URL`
- `MODEL_NAME`
- `HF_TOKEN` as the bearer token for the configured provider

Example:

```bash
API_BASE_URL="https://api.groq.com/openai/v1" \
MODEL_NAME="llama-3.3-70b-versatile" \
HF_TOKEN="$YOUR_API_KEY" \
python inference.py --planner llm
```

## Hugging Face / Docker

The repo includes:

- `openenv.yaml` for the OpenEnv manifest
- `server/Dockerfile` for Spaces deployment
- README front matter for Hugging Face Spaces metadata

Build the image locally:

```bash
docker build -t incident-response-env:latest -f server/Dockerfile .
```
