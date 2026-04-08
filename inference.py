import os
import json
import re
import time
from openai import OpenAI
from incident_response_env import IncidentResponseEnvironment
from incident_response_env.models import IncidentAction
from incident_response_env.agent import HeuristicPlanner
from incident_response_env.scenarios import SCENARIOS

API_BASE_URL = os.getenv("API_BASE_URL", "https://api.groq.com/openai/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "llama-3.3-70b-versatile")
BACKUP_MODEL = os.getenv("BACKUP_MODEL", "llama-3.1-8b-instant")
HF_TOKEN = os.getenv("HF_TOKEN")

SYSTEM_PROMPT = """You are an expert SRE on-call agent triaging a production incident.
You receive the current environment state and must choose the best next action.

RULES:
1. Investigate unhealthy/degraded services FIRST to gather evidence.
2. Do NOT re-investigate a service already in "investigated_services".
3. After gathering evidence, submit_diagnosis for the root cause service with the correct cause string.
4. After diagnosis, apply the correct remediation (rollback, scale_up, or restart).
5. For hard incidents with multiple root causes, handle them one at a time.

Common cause strings: out_of_memory, bad_deploy, connection_leak, cache_memory_pressure, bad_model_deploy

Respond with ONLY a valid JSON object, no markdown, no explanation:
{"type": "investigate|rollback|scale_up|restart|enable_circuit_breaker|submit_diagnosis", "service": "service-name", "cause": "cause-if-diagnosis-else-null"}"""


def build_compact_state(obs):
    """Build a compact state representation to save tokens."""
    services = []
    for s in obs.services:
        if s.status != "healthy":
            services.append({"name": s.name, "status": s.status, "summary": s.summary})
    
    alerts = [{"service": a.service, "severity": a.severity, "message": a.message} for a in obs.alerts]
    
    # Only include logs for unhealthy services
    logs = {}
    metrics = {}
    unhealthy_names = {s.name for s in obs.services if s.status != "healthy"}
    for svc, entries in obs.recent_logs.items():
        if svc in unhealthy_names or svc in obs.investigated_services:
            logs[svc] = entries
    for svc, snapshot in obs.metrics.items():
        if svc in unhealthy_names or svc in obs.investigated_services:
            metrics[svc] = snapshot.model_dump()
    
    return {
        "difficulty": obs.difficulty,
        "feedback": obs.action_feedback,
        "unhealthy_services": services,
        "alerts": alerts,
        "relevant_logs": logs,
        "relevant_metrics": metrics,
        "investigated": obs.investigated_services,
        "diagnosed": obs.diagnosed_services,
        "resolved": obs.resolved_services,
    }


def parse_llm_response(content, observation, heuristic):
    """Parse LLM JSON response into an IncidentAction, falling back to heuristic."""
    if not content:
        return heuristic.next_action(observation), "empty_response"
    
    # Strip markdown fences
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    
    # Find JSON object
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return heuristic.next_action(observation), "no_json_found"
    
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return heuristic.next_action(observation), "json_parse_error"
    
    # Normalize null strings
    for key in ("service", "cause", "notes"):
        val = parsed.get(key)
        if isinstance(val, str) and val.strip().lower() in ("null", "none", ""):
            parsed[key] = None
    
    try:
        action = IncidentAction.model_validate(parsed)
        # Sanity checks: don't re-investigate already investigated services
        if action.type == "investigate" and action.service in observation.investigated_services:
            return heuristic.next_action(observation), None
        return action, None
    except Exception:
        return heuristic.next_action(observation), "validation_error"


def run_episode(level: str):
    env = IncidentResponseEnvironment()
    obs = env.reset(difficulty=level)
    heuristic = HeuristicPlanner()

    benchmark = "incident_response_env"
    print(f"[START] task={level} env={benchmark} model={MODEL_NAME}")

    # Initialize OpenAI Client (required by hackathon spec)
    client = None
    try:
        if HF_TOKEN:
            client = OpenAI(base_url=API_BASE_URL, api_key=HF_TOKEN)
    except Exception:
        pass

    # Conversation history for LLM memory
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    done = False
    step_count = 0
    rewards = []
    try:
        while not done and step_count < 15:
            step_count += 1
            compact_state = build_compact_state(obs)
            user_msg = json.dumps(compact_state, indent=2)
            messages.append({"role": "user", "content": f"Step {step_count}. Current state:\n{user_msg}"})

            error_msg = "null"
            action = None

            if client and HF_TOKEN:
                # Trim conversation history to prevent 413 (keep system + last 6 exchanges)
                if len(messages) > 13:
                    messages = [messages[0]] + messages[-12:]
                
                models_to_try = [MODEL_NAME, BACKUP_MODEL]
                for model_attempt in models_to_try:
                    try:
                        response = client.chat.completions.create(
                            model=model_attempt,
                            messages=messages,
                            temperature=0.1,
                        )
                        content = response.choices[0].message.content
                        messages.append({"role": "assistant", "content": content})
                        action, parse_error = parse_llm_response(content, obs, heuristic)
                        if parse_error:
                            error_msg = parse_error
                        break
                    except Exception as e:
                        err_str = str(e)
                        if ("429" in err_str or "413" in err_str) and model_attempt == MODEL_NAME:
                            time.sleep(1)
                            continue
                        error_msg = err_str.replace(" ", "_").replace("\n", "_")[:100]
                        action = heuristic.next_action(obs)
                        messages.append({"role": "assistant", "content": json.dumps(action.model_dump(exclude_none=True))})
                        break
            
            if action is None:
                action = heuristic.next_action(obs)
                messages.append({"role": "assistant", "content": json.dumps(action.model_dump(exclude_none=True))})

            action_str = f"{action.type}('{action.service}')"
            if action.type == "submit_diagnosis":
                cause_str = (action.cause or "unknown").replace(" ", "_")
                action_str = f"submit_diagnosis('{action.service}','{cause_str}')"

            obs = env.step(action)
            done = obs.done
            reward = obs.reward
            rewards.append(reward)

            print(f"[STEP] step={step_count} action={action_str} reward={reward:.2f} done={str(done).lower()} error={error_msg}")
    finally:
        try:
            env.close()
        except Exception:
            pass

        state = env.state
        success = state.success
        score = state.score_breakdown.total
        rewards_str = ",".join(f"{r:.2f}" for r in rewards)
        if not rewards_str:
            rewards_str = "0.00"

        print(f"[END] success={str(success).lower()} steps={step_count} score={score:.2f} rewards={rewards_str}")
    return score, success


def main():
    levels = list(SCENARIOS)
    scores = {}
    for level in levels:
        score, success = run_episode(level)
        scores[level] = score


if __name__ == "__main__":
    main()
