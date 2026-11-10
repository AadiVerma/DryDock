"""Starting, restarting and configuring the Floci container.

Most scenarios run on Floci's normal settings. A few need a feature switched
on (security group enforcement, or IAM enforcement with task role
credentials). A scenario says so in seed.yaml under `emulator:`, and the lab
restarts Floci with those settings before seeding it.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

from . import config

# seed.yaml `emulator:` key -> docker compose variables (see docker-compose.yml)
SETTINGS = {
    "security_groups": {"ECSLAB_SG_ENFORCEMENT": "true"},
    "iam": {"ECSLAB_IAM_ENFORCEMENT": "true", "ECSLAB_TASK_ROLE_CREDENTIALS": "true"},
}
FLAGS = {"FLOCI_NETWORK_SECURITY_GROUP_ENFORCEMENT_ENABLED": "ECSLAB_SG_ENFORCEMENT",
         "FLOCI_SERVICES_IAM_ENFORCEMENT_ENABLED": "ECSLAB_IAM_ENFORCEMENT",
         "FLOCI_SERVICES_ECS_TASK_ROLE_CREDENTIALS_ENABLED": "ECSLAB_TASK_ROLE_CREDENTIALS"}


class EmulatorError(RuntimeError):
    pass


def sh(cmd, check=True, env=None):
    p = subprocess.run(cmd, cwd=config.ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       env={**os.environ, **(env or {})})
    if check and p.returncode != 0:
        raise EmulatorError(f"{' '.join(cmd)} failed:\n{p.stdout[-1500:]}")
    return p.stdout.strip()


def wanted_env(settings: dict | None) -> dict:
    env = {v: "false" for v in FLAGS.values()}
    for key, on in (settings or {}).items():
        if key not in SETTINGS:
            raise EmulatorError(f"unknown emulator setting '{key}'. Known: {', '.join(sorted(SETTINGS))}")
        if on:
            env.update(SETTINGS[key])
    return env


def current_env() -> dict | None:
    """The switches the running Floci container was started with, or None if it is not running."""
    out = sh(["docker", "inspect", config.FLOCI_CONTAINER, "--format", "{{json .Config.Env}}"], check=False)
    try:
        pairs = dict(e.split("=", 1) for e in json.loads(out) if "=" in e)
    except (ValueError, TypeError):
        return None
    return {var: pairs.get(flag, "false").lower() for flag, var in FLAGS.items()}


def wait(timeout=120):
    aws = config.clients()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            aws["ecs"].list_clusters()
            return
        except Exception:
            time.sleep(2)
    raise EmulatorError(f"Floci did not answer on {config.FLOCI_URL} within {timeout}s. "
                        f"See: docker logs {config.FLOCI_CONTAINER}")


def remove_task_containers():
    ids = sh(["docker", "ps", "-aq", "--filter", f"network={config.DOCKER_NETWORK}"], check=False).split()
    floci = sh(["docker", "ps", "-aq", "--filter", f"name=^{config.FLOCI_CONTAINER}$"], check=False).split()
    ids = [i for i in ids if i not in floci]
    if ids:
        sh(["docker", "rm", "-f", *ids], check=False)


def fresh(settings: dict | None = None):
    """A clean emulator with the given settings. Floci keeps state in memory, so a
    restart is a clean slate; it is recreated only when the settings differ."""
    if config.mock_enabled():
        return
    want = wanted_env(settings)
    remove_task_containers()
    if current_env() == want:
        sh(["docker", "compose", "restart", "floci"])
    else:
        sh(["docker", "compose", "up", "-d", "--force-recreate"], env=want)
    wait()
    remove_task_containers()


def ensure(settings: dict | None = None) -> bool:
    """Make sure Floci runs with these settings. Returns True if it had to be recreated
    (which wipes everything in it)."""
    if config.mock_enabled():
        return False
    if current_env() == wanted_env(settings):
        return False
    fresh(settings)
    return True
