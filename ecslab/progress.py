"""What the learner has solved, kept on this machine only.

Lives beside the lab's state but survives `ecslab reset`, which throws away
the environment, not the learner's history.
"""
from __future__ import annotations

import json
import time

from . import config

KEEP_ATTEMPTS = 10


def _path():
    return config.STATE_DIR / "progress.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def record_pass(scenario_id: str, seconds: float, hints: int, calls: int, replay_text: str) -> dict:
    data = load()
    entry = data.setdefault(scenario_id, {"solved": False, "best_seconds": None, "attempts": []})
    entry["solved"] = True
    seconds = round(seconds)
    if entry["best_seconds"] is None or seconds < entry["best_seconds"]:
        entry["best_seconds"] = seconds
    entry["attempts"].append({"at": time.time(), "seconds": seconds, "hints": hints, "calls": calls, "replay": replay_text})
    entry["attempts"] = entry["attempts"][-KEEP_ATTEMPTS:]
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(data, indent=2))
    return entry


def forget():
    _path().unlink(missing_ok=True)
