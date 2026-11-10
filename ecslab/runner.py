"""Load a scenario, seed it, and decide whether the learner has fixed it."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config, seeder
from .assertions import Ctx, evaluate

PASSED, FORBIDDEN, PARTIAL, NOT_STARTED, FAILING = "passed", "forbidden", "partial", "not_started", "failing"


@dataclass
class Outcome:
    status: str
    messages: list = field(default_factory=list)   # shown to the learner
    details: list = field(default_factory=list)    # (rule name, ok, reason)


def available() -> list[str]:
    return sorted(p.parent.name for p in config.SCENARIOS_DIR.glob("*/scenario.yaml"))


def load(scenario_id: str) -> dict:
    path = config.SCENARIOS_DIR / scenario_id / "scenario.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no scenario '{scenario_id}'. Available: {', '.join(available()) or 'none'}")
    scenario = yaml.safe_load(path.read_text())
    scenario["seed_spec"] = yaml.safe_load((path.parent / scenario.get("seed", "seed.yaml")).read_text())
    scenario.setdefault("cluster", scenario["seed_spec"]["cluster"])
    return scenario


def start(aws, scenario: dict, wait: bool = True, sleep=time.sleep) -> dict:
    """Seed the scenario and record that it is the active one."""
    summary = seeder.seed(aws, scenario["seed_spec"], wait=wait, sleep=sleep)
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    config.CALL_LOG.write_text("")           # the learner's trail starts now
    state = {"scenario": scenario["id"], "started_at": time.time(), "hints_shown": 0, "solved_at": None}
    save_state(state)
    return summary


def load_state() -> dict | None:
    if config.STATE_FILE.exists():
        return json.loads(config.STATE_FILE.read_text())
    return None


def save_state(state: dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    config.STATE_FILE.write_text(json.dumps(state, indent=2))


def read_calls(path: Path | None = None) -> list[dict]:
    path = path or config.CALL_LOG
    if not path.exists():
        return []
    calls = []
    for line in path.read_text().splitlines():
        try:
            calls.append(json.loads(line))
        except ValueError:
            continue
    return calls


def _untouched(aws, scenario, calls) -> bool:
    """True when nothing has been deployed: no UpdateService in the trail and the
    service is still on the seed's task definition."""
    if any(c.get("action") in ("UpdateService", "CreateService", "DeleteService") for c in calls):
        return False
    svc_spec = scenario["seed_spec"]["service"]
    found = aws["ecs"].describe_services(cluster=scenario["cluster"],
                                         services=[svc_spec["name"]]).get("services", [])
    return bool(found) and found[0].get("taskDefinition", "").endswith("/" + svc_spec["task_definition"])


def check(aws, scenario: dict, calls: list[dict] | None = None, sleep=time.sleep,
          settle_seconds: float = 8) -> Outcome:
    """Forbidden shortcuts are applied before success rules, so hiding the
    symptom never passes."""
    calls = calls if calls is not None else read_calls()
    ctx = Ctx(cluster=scenario["cluster"], seed=scenario["seed_spec"], sleep=sleep, settle_seconds=settle_seconds)

    violations = []
    for rule in scenario.get("forbid", []):
        name, res = evaluate(rule, aws, ctx)
        if res.ok:
            violations.append((name, res.reason))
    if violations:
        return Outcome(FORBIDDEN,
                       ["That makes the symptom go away without fixing anything:"]
                       + [f"  - {reason}" for _, reason in violations]
                       + ["Undo it and fix the cause."],
                       [(n, True, r) for n, r in violations])

    if not scenario["seed_spec"].get("incident") and _untouched(aws, scenario, calls):
        msg = ["Nothing has been deployed yet. Read the brief and get to work."]
        if not calls:
            msg.append(f"(No AWS calls recorded either. Is AWS_ENDPOINT_URL set to {config.PROXY_URL}?)")
        return Outcome(NOT_STARTED, msg)

    details, all_ok = [], True
    for rule in scenario.get("success", []):
        name, res = evaluate(rule, aws, ctx)
        details.append((name, res.ok, res.reason))
        all_ok = all_ok and res.ok
    if all_ok:
        return Outcome(PASSED, [], details)

    for rule in scenario.get("partial", []):
        (pname, body), = rule.items()
        _, res = evaluate(body["when"], aws, ctx)
        if res.ok:
            return Outcome(PARTIAL, [body["message"].rstrip()], details + [(pname, True, res.reason)])

    return Outcome(FAILING, ["Not fixed yet:"] + [f"  - {reason}" for _, ok, reason in details if not ok], details)


def next_hint(scenario: dict, state: dict, now: float | None = None) -> str:
    hints = scenario.get("hints", [])
    shown = state.get("hints_shown", 0)
    elapsed_min = ((now or time.time()) - state["started_at"]) / 60
    if shown >= len(hints):
        return "No more hints. Last one was:\n  " + hints[-1]["text"] if hints else "This scenario has no hints."
    hint = hints[shown]
    if elapsed_min < hint.get("after_minutes", 0):
        wait = hint["after_minutes"] - elapsed_min
        prev = f"\nLast hint was:\n  {hints[shown - 1]['text']}" if shown else ""
        return f"Next hint unlocks in {wait:.0f} min. Keep digging.{prev}"
    state["hints_shown"] = shown + 1
    save_state(state)
    return f"Hint {shown + 1} of {len(hints)}:\n  {hint['text']}"
