"""Paths, endpoints and AWS clients."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = ROOT / "scenarios"
STATE_DIR = Path(os.environ.get("ECSLAB_STATE_DIR", ROOT / ".ecslab"))
STATE_FILE = STATE_DIR / "state.json"
CALL_LOG = STATE_DIR / "calls.jsonl"
PROXY_PID = STATE_DIR / "proxy.pid"
PROXY_LOG = STATE_DIR / "proxy.log"
MOCK_STATE = STATE_DIR / "mock_state.json"

FLOCI_URL = os.environ.get("ECSLAB_FLOCI_URL", "http://localhost:4566")
PROXY_PORT = int(os.environ.get("ECSLAB_PROXY_PORT", "4567"))
PROXY_URL = f"http://localhost:{PROXY_PORT}"
UI_PORT = int(os.environ.get("ECSLAB_UI_PORT", "4568"))
CONSOLE_AGENT = "ecslab-console"   # marks calls made by clicking in the web interface
REGION = "us-east-1"
ACCOUNT = "000000000000"
FLOCI_CONTAINER = "ecslab-floci"
DOCKER_NETWORK = "ecslab"

SERVICES = ("ecs", "elbv2", "ec2", "logs", "iam", "ssm", "secretsmanager", "servicediscovery", "s3")


def mock_enabled() -> bool:
    return os.environ.get("ECSLAB_MOCK") == "1"


def learner_env() -> dict:
    """Environment that points the AWS CLI at the lab proxy."""
    return {"AWS_ENDPOINT_URL": PROXY_URL, "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_DEFAULT_REGION": REGION, "AWS_PAGER": ""}


def clients(as_learner: bool = False) -> dict:
    """AWS clients.

    By default these are for ECS Lab's own use: they go straight to Floci, not
    through the logging proxy, so the lab's calls never appear in the learner's
    trail. With as_learner=True they go through the proxy and are logged: the web
    interface uses that when the learner clicks, so clicking counts in the replay
    exactly like typing the equivalent AWS CLI command."""
    if mock_enabled():
        from .mock import FakeAWS
        fake = FakeAWS(persist=MOCK_STATE)
        return {name: fake.client(name) for name in SERVICES}
    import boto3
    from botocore.config import Config
    cfg = Config(retries={"max_attempts": 2}, connect_timeout=5, read_timeout=30,
                 user_agent_extra=CONSOLE_AGENT if as_learner else None)
    return {name: boto3.client(name, endpoint_url=PROXY_URL if as_learner else FLOCI_URL, region_name=REGION, config=cfg,
                               aws_access_key_id="test", aws_secret_access_key="test")
            for name in SERVICES}
