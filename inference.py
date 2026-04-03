from __future__ import annotations

import argparse
import json
from typing import Iterable

from incident_response_env import IncidentResponseEnvironment, build_planner


def run_episode(level: str, planner_mode: str, episode_number: int) -> dict[str, object]:
    env = IncidentResponseEnvironment()
    planner = build_planner(planner_mode)
    observation = env.reset(difficulty=level)

    print_json(
        "START",
        {
            "task_id": level,
            "episode": episode_number,
            "planner": planner_mode,
        },
    )

    cumulative_reward = 0.0
    while not observation.done:
        action = planner.next_action(observation)
        observation = env.step(action)
        cumulative_reward = observation.score_breakdown.total
        print_json(
            "STEP",
            {
                "step": observation.metadata["step_count"],
                "action": action.model_dump(exclude_none=True),
                "reward": observation.reward,
                "done": observation.done,
                "cumulative_reward": round(cumulative_reward, 4),
            },
        )

    state = env.state
    result = {
        "task_id": level,
        "episode": episode_number,
        "total_reward": state.score_breakdown.total,
        "steps": state.step_count,
        "success": state.success,
        "failed_actions": state.failed_actions,
    }
    print_json("END", result)
    return result


def print_json(prefix: str, payload: dict[str, object]) -> None:
    print(f"[{prefix}] {json.dumps(payload, sort_keys=True)}")


def iter_levels(raw_levels: Iterable[str]) -> list[str]:
    levels = [level.strip().lower() for level in raw_levels if level.strip()]
    return levels or ["easy", "medium", "hard"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run IncidentResponseEnv episodes.")
    parser.add_argument(
        "--planner",
        default="auto",
        choices=["auto", "heuristic", "llm"],
        help="Planner mode. 'auto' uses an OpenAI-compatible endpoint when configured, otherwise heuristics.",
    )
    parser.add_argument(
        "--levels",
        nargs="*",
        default=["easy", "medium", "hard"],
        help="Difficulty levels to run.",
    )
    parser.add_argument(
        "--episodes",
        type=int,
        default=1,
        help="Episodes per level.",
    )
    args = parser.parse_args()

    summary: list[dict[str, object]] = []
    for level in iter_levels(args.levels):
        for episode_number in range(1, args.episodes + 1):
            summary.append(run_episode(level, args.planner, episode_number))

    print_json(
        "SUMMARY",
        {
            "episodes": len(summary),
            "mean_reward": round(
                sum(float(item["total_reward"]) for item in summary) / max(len(summary), 1),
                4,
            ),
            "successes": sum(1 for item in summary if item["success"]),
        },
    )


if __name__ == "__main__":
    main()
