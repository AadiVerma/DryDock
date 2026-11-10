#!/usr/bin/env python3
"""Which ECS failure modes can Floci actually reproduce?

Scenarios are only worth writing for failures the emulator really produces.
This launches one small service per candidate failure mode, watches them all
for about 90 seconds, and prints what each one looked like: how many tasks
were launched and stopped, the stop code, stoppedReason, exit codes, health
and the last log lines.

    ecslab start                      # Floci must be running, images built
    python preflight/classes.py
    python preflight/classes.py --only oom,bad-image --observe 120

Nothing is faked. Full evidence is written to preflight/results/classes-*.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from botocore.exceptions import ClientError   # noqa: E402

from ecslab import config                     # noqa: E402

IMAGE = "ecslab/checkout-api:1.4.2"
CLUSTER = "probe-classes"
ROLE = f"arn:aws:iam::{config.ACCOUNT}:role/ecsTaskExecutionRole"

# name, what it models on AWS, changes to the container / task definition / extras
PROBES = [
    dict(name="control", models="A healthy service (baseline; everything else is compared with this)"),
    dict(name="missing-env", models="App exits at startup: required config missing",
         env={"REQUIRE_ENV": "PAYMENTS_API_URL"}),
    dict(name="bad-image", models="Image tag does not exist: CannotPullContainerError",
         container={"image": "ecslab/checkout-api:9.9.9-does-not-exist"}),
    dict(name="bad-command", models="Command not found in the image: exit 127 / CannotStartContainerError",
         container={"command": ["/usr/local/bin/start-checkout"]}),
    dict(name="bad-args", models="Entrypoint runs but the script path is wrong: exit 2",
         container={"command": ["python", "/app/main.py"]}),
    dict(name="oom", models="Container exceeds its memory limit: OutOfMemoryError, exit 137",
         env={"MEMORY_HOG_MB": "300"}, container={"memory": 64}),
    dict(name="crash-later", models="Starts fine, crashes after 15s: tasks flap",
         env={"EXIT_AFTER_SECONDS": "15", "EXIT_CODE": "3"}),
    dict(name="essential-sidecar", models="An essential sidecar exits, taking the whole task down",
         sidecar={"name": "config-loader", "essential": True, "command": ["sh", "-c", "echo sidecar: config sync failed; exit 4"]}),
    dict(name="optional-sidecar", models="A non-essential sidecar exits; the task should keep running",
         sidecar={"name": "config-loader", "essential": False, "command": ["sh", "-c", "echo sidecar: done; exit 0"]}),
    dict(name="depends-on", models="App waits for a dependency container that fails to start",
         sidecar={"name": "migrate", "essential": False, "command": ["sh", "-c", "echo migrate: schema error; exit 1"]},
         container={"dependsOn": [{"containerName": "migrate", "condition": "SUCCESS"}]}),
    dict(name="secret-missing", models="Secret reference does not exist: ResourceInitializationError",
         container={"secrets": [{"name": "DB_PASSWORD", "valueFrom":
                                 f"arn:aws:ssm:{config.REGION}:{config.ACCOUNT}:parameter/shop/prod/does-not-exist"}]},
         env={"REQUIRE_ENV": "DB_PASSWORD"}),
    dict(name="secret-present", models="Secret from SSM Parameter Store is injected as an environment variable",
         ssm={"/shop/prod/db-password": "s3cret"},
         container={"secrets": [{"name": "DB_PASSWORD", "valueFrom":
                                 f"arn:aws:ssm:{config.REGION}:{config.ACCOUNT}:parameter/shop/prod/db-password"}]},
         env={"REQUIRE_ENV": "DB_PASSWORD"}),
    dict(name="readonly-fs", models="readonlyRootFilesystem set, app needs to write: exits at startup",
         env={"WRITE_PATH": "/app/cache.db"}, container={"readonlyRootFilesystem": True}),
    dict(name="bad-user", models="Container user does not exist in the image: fails to start",
         container={"user": "checkout"}),
    dict(name="container-healthcheck", models="Container health check fails: task UNHEALTHY and replaced",
         container={"healthCheck": {"command": ["CMD-SHELL", "exit 1"], "interval": 5, "timeout": 2, "retries": 2, "startPeriod": 0}}),
    dict(name="dependency-down", models="App cannot reach a service it needs at startup: exits",
         env={"REQUIRE_HTTP": "http://inventory.shop.internal:8080/health"}),
    dict(name="circuit-breaker", models="Deployment circuit breaker with rollback: failed deploy rolls back by itself",
         env={"REQUIRE_ENV": "PAYMENTS_API_URL"}, deploy_over_control=True,
         service={"deploymentConfiguration": {"deploymentCircuitBreaker": {"enable": True, "rollback": True}}}),
    dict(name="lb-wrong-path", models="Target group health check path is wrong: targets unhealthy",
         lb={"HealthCheckPath": "/healthz"}),
    dict(name="lb-wrong-code", models="App health endpoint returns 503: targets unhealthy",
         env={"HEALTH_STATUS": "503"}, lb={}),
    dict(name="lb-slow-start", models="App takes 25s to start listening: targets unhealthy at first, then healthy",
         env={"STARTUP_DELAY_SECONDS": "25"}, lb={}),
    dict(name="lb-wrong-port", models="Service registers the wrong container port with the load balancer",
         env={"PORT": "9000"}, lb={}),
    dict(name="invalid-size", models="Invalid Fargate CPU/memory pair: rejected at registration",
         td={"cpu": "256", "memory": "8192"}),
    dict(name="stop-ignored", models="App ignores SIGTERM: a stop has to kill it (exit 137)",
         env={"IGNORE_SIGTERM": "1"}, stop_after=20),
]


def err(e):
    if isinstance(e, ClientError):
        return f"{e.response['Error'].get('Code')}: {e.response['Error'].get('Message')}"
    return f"{type(e).__name__}: {e}"


def strip(x):
    if isinstance(x, dict):
        return {k: strip(v) for k, v in x.items() if k != "ResponseMetadata"}
    if isinstance(x, list):
        return [strip(v) for v in x]
    return x.isoformat() if isinstance(x, (dt.datetime, dt.date)) else x


def task_definition(p):
    name = f"probe-{p['name']}"
    env = {"PORT": "8080", **p.get("env", {})}
    logs = {"logDriver": "awslogs", "options": {"awslogs-group": f"/probe/{p['name']}", "awslogs-region": config.REGION,
                                                "awslogs-stream-prefix": "ecs", "awslogs-create-group": "true"}}
    app = {"name": "app", "image": IMAGE, "essential": True, "portMappings": [{"containerPort": 8080, "protocol": "tcp"}],
           "environment": [{"name": k, "value": v} for k, v in env.items()], "logConfiguration": logs}
    app.update(p.get("container", {}))
    containers = [app]
    if p.get("sidecar"):
        containers.append({"image": IMAGE, "logConfiguration": logs, **p["sidecar"]})
    td = dict(family=name, networkMode="awsvpc", requiresCompatibilities=["FARGATE"], cpu="256", memory="512",
              executionRoleArn=ROLE, containerDefinitions=containers)
    td.update(p.get("td", {}))
    return {k: v for k, v in td.items() if v is not None}   # a None value removes the field


_PORTS = itertools.count(19000)   # one listener port per probe load balancer


def launch(aws, p, subnets, sg, vpc):
    """Create everything for one probe. Returns a dict of what was created, or raises."""
    ecs, name = aws["ecs"], f"probe-{p['name']}"
    for key, value in p.get("ssm", {}).items():
        aws["ssm"].put_parameter(Name=key, Value=value, Type="SecureString", Overwrite=True)
    made = {"service": name}
    arn = ecs.register_task_definition(**task_definition(p))["taskDefinition"]["taskDefinitionArn"]
    sg = p.get("sg") or sg
    kw = dict(cluster=CLUSTER, serviceName=name, taskDefinition=arn, desiredCount=1, launchType="FARGATE",
              networkConfiguration={"awsvpcConfiguration": {"subnets": subnets, "securityGroups": [sg],
                                                            "assignPublicIp": "ENABLED"}}, **p.get("service", {}))
    if p.get("deploy_over_control"):   # start healthy, then deploy the broken revision on top
        good = ecs.register_task_definition(**task_definition({"name": p["name"]}))["taskDefinition"]["taskDefinitionArn"]
        kw["taskDefinition"], made["then_deploy"], made["good"] = good, arn, good
    if "lb" in p:
        hc = dict(HealthCheckProtocol="HTTP", HealthCheckPath="/health", HealthCheckPort="traffic-port",
                  HealthCheckIntervalSeconds=5, HealthCheckTimeoutSeconds=2, HealthyThresholdCount=2,
                  UnhealthyThresholdCount=2, Matcher={"HttpCode": "200"})
        hc.update(p["lb"])
        tg = aws["elbv2"].create_target_group(Name=f"p-{p['name']}"[:32], Protocol="HTTP", Port=8080, VpcId=vpc,
                                              TargetType=p.get("tg_type", "ip"), **hc)["TargetGroups"][0]["TargetGroupArn"]
        lb = aws["elbv2"].create_load_balancer(Name=f"p-{p['name']}"[:32], Subnets=subnets, SecurityGroups=[sg],
                                               Scheme="internet-facing", Type="application")["LoadBalancers"][0]["LoadBalancerArn"]
        aws["elbv2"].create_listener(LoadBalancerArn=lb, Protocol="HTTP", Port=next(_PORTS),
                                     DefaultActions=[{"Type": "forward", "TargetGroupArn": tg}])
        kw["loadBalancers"] = [{"targetGroupArn": tg, "containerName": p.get("lb_container", "app"), "containerPort": 8080}]
        made["tg"] = tg
    ecs.create_service(**kw)
    return made


def observe(aws, name, made):
    ecs = aws["ecs"]
    out = {}
    svc = ecs.describe_services(cluster=CLUSTER, services=[name])["services"][0]
    primary = next((d for d in svc.get("deployments", []) if d.get("status") == "PRIMARY"), {})
    out.update(running=svc.get("runningCount"), desired=svc.get("desiredCount"), rollout=primary.get("rolloutState"),
               taskDefinition=svc.get("taskDefinition", "").rsplit("/", 1)[-1],
               events=[e.get("message") for e in svc.get("events", [])][:3])
    arns = []
    for status in ("RUNNING", "STOPPED"):
        arns += ecs.list_tasks(cluster=CLUSTER, serviceName=name, desiredStatus=status).get("taskArns", [])
    tasks = ecs.describe_tasks(cluster=CLUSTER, tasks=arns[:100]).get("tasks", []) if arns else []
    stopped = [t for t in tasks if t.get("lastStatus") == "STOPPED"]
    out.update(
        launched=len(tasks), stopped=len(stopped),
        stopCodes=sorted({t.get("stopCode") or "-" for t in stopped}),
        stoppedReasons=sorted({t.get("stoppedReason") or "-" for t in stopped}),
        exitCodes=sorted({f"{c.get('name')}={c.get('exitCode')}" for t in stopped for c in t.get("containers", [])}),
        containerReasons=sorted({c.get("reason") for t in tasks for c in t.get("containers", []) if c.get("reason")}),
        health=sorted({t.get("healthStatus") or "-" for t in tasks}),
        lastStatuses=sorted({t.get("lastStatus") or "-" for t in tasks}))
    if made.get("tg"):
        th = aws["elbv2"].describe_target_health(TargetGroupArn=made["tg"])["TargetHealthDescriptions"]
        out["targets"] = sorted(f"{t['TargetHealth'].get('State')}" + (f"({t['TargetHealth'].get('Reason')})"
                                if t["TargetHealth"].get("Reason") else "") for t in th)
    try:
        ev = aws["logs"].filter_log_events(logGroupName=f"/probe/{name[6:]}", limit=200).get("events", [])
        seen = []
        for e in ev:
            line = (e.get("message") or "").strip()
            if line and line not in seen and "GET /health" not in line:
                seen.append(line)
        out["logs"] = seen[-4:]
    except Exception as e:
        out["logs"] = [f"<no logs: {err(e)}>"]
    return out


def verdict(o):
    if "error" in o:
        return "REJECTED"
    if o["stopped"] >= 2:
        return "LOOPS"
    if o["stopped"] == 1 and not o["running"]:
        return "STOPS"
    if o["stopped"] == 0 and o["launched"] == 0:
        return "NOTHING RUNS"
    return "RUNS"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--observe", type=int, default=90, help="seconds to watch (default 90)")
    ap.add_argument("--only", default="", help="comma-separated probe names")
    ap.add_argument("--keep", action="store_true", help="leave the probe services running")
    args = ap.parse_args()
    probes = [p for p in PROBES if not args.only or p["name"] in args.only.split(",")]

    import boto3
    from botocore.config import Config
    cfg = Config(retries={"max_attempts": 2}, connect_timeout=5, read_timeout=30)
    aws = {n: boto3.client(n, endpoint_url=config.FLOCI_URL, region_name=config.REGION, config=cfg,
                           aws_access_key_id="test", aws_secret_access_key="test")
           for n in ("ecs", "elbv2", "ec2", "logs", "iam", "ssm")}
    try:
        aws["ecs"].list_clusters()
    except Exception as e:
        sys.exit(f"Floci is not answering on {config.FLOCI_URL} ({err(e)}). Run `ecslab start` first.")

    aws["ecs"].create_cluster(clusterName=CLUSTER)
    vpc = aws["ec2"].describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subnets = [s["SubnetId"] for s in aws["ec2"].describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["Subnets"]][:2]
    sg = aws["ec2"].describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["SecurityGroups"][0]["GroupId"]

    print(f"Launching {len(probes)} probes ...")
    made, results = {}, {}
    for p in probes:
        try:
            made[p["name"]] = launch(aws, p, subnets, sg, vpc)
        except Exception as e:
            results[p["name"]] = {"error": err(e)}
            print(f"  {p['name']:<22} rejected: {err(e)[:110]}")
    start = time.time()
    did_second = set()
    print(f"Watching for {args.observe}s ...")
    while time.time() - start < args.observe:
        elapsed = time.time() - start
        for p in probes:
            m = made.get(p["name"])
            if not m:
                continue
            try:
                if m.get("then_deploy") and elapsed > 25 and p["name"] not in did_second:
                    aws["ecs"].update_service(cluster=CLUSTER, service=m["service"], taskDefinition=m["then_deploy"])
                    did_second.add(p["name"])
                if p.get("stop_after") and elapsed > p["stop_after"] and p["name"] not in did_second:
                    for arn in aws["ecs"].list_tasks(cluster=CLUSTER, serviceName=m["service"]).get("taskArns", []):
                        aws["ecs"].stop_task(cluster=CLUSTER, task=arn, reason="probe: stopping to test SIGTERM handling")
                    did_second.add(p["name"])
            except Exception as e:
                results[p["name"]] = {"error": "second step failed: " + err(e)}
        time.sleep(5)

    for p in probes:
        m = made.get(p["name"])
        if m and p["name"] not in results:
            try:
                results[p["name"]] = observe(aws, m["service"], m)
                if m.get("good"):
                    results[p["name"]]["rolledBack"] = results[p["name"]]["taskDefinition"] == m["good"].rsplit("/", 1)[-1]
            except Exception as e:
                results[p["name"]] = {"error": "could not observe: " + err(e)}

    print("\n" + "=" * 110)
    print(f"{'PROBE':<22}{'RESULT':<14}WHAT FLOCI DID")
    print("=" * 110)
    for p in probes:
        o = results[p["name"]]
        print(f"{p['name']:<22}{verdict(o):<14}{p['models']}")
        if "error" in o:
            print(f"{'':<36}{o['error'][:200]}")
            continue
        print(f"{'':<36}launched {o['launched']}, stopped {o['stopped']}, running {o['running']}/{o['desired']}, "
              f"rollout {o['rollout']}, lastStatus {o['lastStatuses']}, health {o['health']}")
        if o["stopped"]:
            print(f"{'':<36}stopCode {o['stopCodes']}  stoppedReason {o['stoppedReasons']}  exit {o['exitCodes']}")
        if o["containerReasons"]:
            print(f"{'':<36}container reason {o['containerReasons']}")
        if "targets" in o:
            print(f"{'':<36}targets {o['targets']}")
        if "rolledBack" in o:
            print(f"{'':<36}rolled back to the good revision: {o['rolledBack']}")
        if o.get("logs"):
            print(f"{'':<36}logs: " + " | ".join(o["logs"])[:230])
    print("=" * 110)

    out_dir = ROOT / "preflight" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"classes-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(strip({"observe_seconds": args.observe, "results": results}), indent=2, default=str))
    print(f"Evidence: {out.relative_to(ROOT)}")

    if not args.keep:
        for m in made.values():
            try:
                aws["ecs"].update_service(cluster=CLUSTER, service=m["service"], desiredCount=0)
                aws["ecs"].delete_service(cluster=CLUSTER, service=m["service"], force=True)
            except Exception:
                pass
        print("Probe services removed. Run `ecslab reset` for a completely clean emulator.")


if __name__ == "__main__":
    main()
