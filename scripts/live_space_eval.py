from __future__ import annotations

import json
import os

import requests
from huggingface_hub import HfApi

from incident_response_env.agent import OpenAICompatiblePlanner
from incident_response_env.models import IncidentObservation


SPACE_URL = "https://abhi1203-incident-response-env.hf.space"
SPACE_REPO_ID = "Abhi1203/incident-response-env"


def main() -> None:
    api = HfApi()
    variables = api.get_space_variables(SPACE_REPO_ID)
    os.environ["API_BASE_URL"] = variables["API_BASE_URL"].value
    os.environ["MODEL_NAME"] = variables["MODEL_NAME"].value

    planner = OpenAICompatiblePlanner(
        api_base_url=variables["API_BASE_URL"].value,
        model_name=variables["MODEL_NAME"].value,
        api_key=variables["HF_TOKEN"].value,
    )

    results: list[dict[str, object]] = []
    for difficulty in ["easy", "medium", "hard"]:
        reset_response = requests.post(
            SPACE_URL + "/reset",
            json={"difficulty": difficulty},
            timeout=30,
        )
        reset_response.raise_for_status()
        payload = reset_response.json()
        observation = IncidentObservation.model_validate(payload["observation"])
        print("[START]", json.dumps({"task_id": difficulty, "episode": 1}, sort_keys=True), flush=True)

        steps = 0
        done = bool(payload.get("done"))
        total_reward = float(payload.get("reward") or 0.0)
        while not done and steps < 15:
            action = planner.next_action(observation)
            step_response = requests.post(
                SPACE_URL + "/step",
                json={"action": action.model_dump(exclude_none=True)},
                timeout=30,
            )
            step_response.raise_for_status()
            step_payload = step_response.json()
            observation = IncidentObservation.model_validate(step_payload["observation"])
            steps += 1
            done = bool(step_payload.get("done"))
            total_reward = observation.score_breakdown.total
            print(
                "[STEP]",
                json.dumps(
                    {
                        "step": steps,
                        "action": action.model_dump(exclude_none=True),
                        "reward": step_payload.get("reward"),
                        "done": done,
                        "cumulative_reward": round(total_reward, 4),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

        result = {
            "task_id": difficulty,
            "episode": 1,
            "total_reward": round(total_reward, 4),
            "steps": steps,
            "success": bool(
                observation.score_breakdown.restoration > 0
                and observation.score_breakdown.diagnosis > 0
                and done
            ),
        }
        results.append(result)
        print("[END]", json.dumps(result, sort_keys=True), flush=True)

    print(
        "[SUMMARY]",
        json.dumps(
            {
                "episodes": len(results),
                "mean_reward": round(sum(float(item["total_reward"]) for item in results) / len(results), 4),
                "successes": sum(1 for item in results if item["success"]),
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
