#!/usr/bin/env python3
"""ECS Lab preflight probe (build plan step 1).

Brings up Floci, builds the task image, and finds out -- with evidence -- what
Floci's ECS + ELBv2 actually do. Nothing here is faked: every row in the final
table comes from a real API response, and the full responses are written to
preflight/results/.

    python preflight/preflight.py              # full run, tears down afterwards
    python preflight/preflight.py --keep       # leave Floci up for poking around
    python preflight/preflight.py --observe 240  # watch the failure loop longer

Exit code: 0 scenario 1 viable as written, 2 not viable (see fallbacks in the
output), 1 the probe itself could not run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    sys.exit("boto3 is not installed. Run: pip install -r requirements.txt")

ROOT = Path(__file__).resolve().parent.parent
IMAGE_DIR = ROOT / "images" / "checkout-api"
RESULTS_DIR = ROOT / "preflight" / "results"
IMAGE = "ecslab/checkout-api:local"
REGION = "us-east-1"
ACCOUNT = "000000000000"

PASS, FAIL, PARTIAL, SKIP = "PASS", "FAIL", "PARTIAL", "SKIP"
COLOUR = {PASS: "\033[32m", FAIL: "\033[31m", PARTIAL: "\033[33m", SKIP: "\033[90m"}
RESET = "\033[0m"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

class Report:
    def __init__(self):
        self.rows = []       # (key, title, status, note)
        self.evidence = {}   # key -> anything JSON-serialisable
        self.started = time.time()

    def add(self, key, title, status, note, evidence=None):
        self.rows.append((key, title, status, note))
        if evidence is not None:
            self.evidence[key] = evidence
        c = COLOUR[status] if sys.stdout.isatty() else ""
        r = RESET if sys.stdout.isatty() else ""
        print(f"  {c}[{status}]{r} {title}\n         {note}", flush=True)

    def status(self, key):
        for k, _, s, _ in self.rows:
            if k == key:
                return s
        return None


def log(msg):
    print(f"\n== {msg}", flush=True)


def strip(resp):
    """Drop boto3's ResponseMetadata so evidence is just the API payload."""
    if isinstance(resp, dict):
        return {k: strip(v) for k, v in resp.items() if k != "ResponseMetadata"}
    if isinstance(resp, list):
        return [strip(v) for v in resp]
    if isinstance(resp, (dt.datetime, dt.date)):
        return resp.isoformat()
    return resp


def err(e):
    if isinstance(e, ClientError):
        return f"{e.response['Error'].get('Code')}: {e.response['Error'].get('Message')}"
    return f"{type(e).__name__}: {e}"


def sh(cmd, check=True, capture=True, cwd=ROOT):
    p = subprocess.run(cmd, cwd=cwd, text=True,
                       stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.STDOUT if capture else None)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({p.returncode}):\n{p.stdout or ''}")
    return (p.stdout or "").strip()


def short(arn):
    return arn.rsplit("/", 1)[-1][:12] if arn else "?"


# --------------------------------------------------------------------------- #
# Environment: docker, compose, image
# --------------------------------------------------------------------------- #

def environment(args, rep):
    log("Environment")
    if args.no_compose:
        rep.add("env", "Floci via docker compose", SKIP, f"--no-compose: using {args.endpoint} as-is")
        return True
    try:
        ver = sh(["docker", "version", "--format", "{{.Server.Version}}"])
    except Exception as e:
        rep.add("env", "Docker daemon reachable", FAIL, f"Is Docker Desktop running? {str(e)[:200]}")
        return False

    if not args.no_build:
        try:
            sh(["docker", "build", "-q", "-t", IMAGE, str(IMAGE_DIR)])
            rep.add("image", "Build local task image", PASS, f"{IMAGE} (listens on $PORT)")
        except Exception as e:
            rep.add("image", "Build local task image", FAIL, str(e)[-400:])
            return False

    try:
        sh(["docker", "compose", "down", "-v", "--remove-orphans"], check=False)
        leftovers = sh(["docker", "ps", "-aq", "--filter", "network=ecslab"], check=False).split()
        if leftovers:
            sh(["docker", "rm", "-f", *leftovers], check=False)
        sh(["docker", "compose", "up", "-d"])
        info = sh(["docker", "inspect", "ecslab-floci", "--format",
                   "{{.Config.Image}} {{.Image}}"], check=False)
        labels = sh(["docker", "inspect", "ecslab-floci", "--format",
                     "{{json .Config.Labels}}"], check=False)
        rep.add("env", "Floci via docker compose", PASS,
                f"docker {ver}; {info}", {"image": info, "labels": labels})
    except Exception as e:
        rep.add("env", "Floci via docker compose", FAIL, str(e)[-400:])
        return False
    return True


def wait_for_floci(clients, rep, timeout=120):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            clients["ecs"].list_clusters()
            rep.add("api", "Floci answers AWS API calls on the endpoint", PASS, "ecs:ListClusters returned")
            return True
        except Exception as e:
            last = e
            time.sleep(2)
    rep.add("api", "Floci answers AWS API calls on the endpoint", FAIL, f"no answer in {timeout}s: {err(last)}")
    return False


def floci_logs(n=60):
    try:
        return sh(["docker", "logs", "--tail", str(n), "ecslab-floci"], check=False)
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# AWS resource setup
# --------------------------------------------------------------------------- #

def task_def(family, port_env, log_group, extra_env=None, health_check=None):
    env = [{"name": "PORT", "value": str(port_env)}] + (extra_env or [])
    container = {
        "name": "app",
        "image": IMAGE,
        "essential": True,
        "portMappings": [{"containerPort": 8080, "protocol": "tcp"}],
        "environment": env,
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": log_group,
                "awslogs-region": REGION,
                "awslogs-stream-prefix": "ecs",
                "awslogs-create-group": "true",
            },
        },
    }
    if health_check:
        container["healthCheck"] = health_check
    return dict(
        family=family,
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="256",
        memory="512",
        executionRoleArn=f"arn:aws:iam::{ACCOUNT}:role/ecsTaskExecutionRole",
        containerDefinitions=[container],
    )


def network(clients, rep):
    """Default VPC + two subnets + a security group (an ALB needs two subnets)."""
    ec2 = clients["ec2"]
    try:
        vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
        if not vpcs:
            vpcs = [ec2.create_default_vpc()["Vpc"]]
        vpc = vpcs[0]["VpcId"]
        subnets = [s["SubnetId"] for s in
                   ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["Subnets"]]
        if len(subnets) < 2:
            zones = [z["ZoneName"] for z in ec2.describe_availability_zones()["AvailabilityZones"]][:2]
            for i, z in enumerate(zones):
                subnets.append(ec2.create_subnet(
                    VpcId=vpc, CidrBlock=f"172.31.{200 + i}.0/24", AvailabilityZone=z)["Subnet"]["SubnetId"])
        sgs = ec2.describe_security_groups(
            Filters=[{"Name": "vpc-id", "Values": [vpc]}, {"Name": "group-name", "Values": ["default"]}]
        )["SecurityGroups"]
        sg = sgs[0]["GroupId"] if sgs else ec2.create_security_group(
            GroupName="ecslab-probe", Description="ecslab probe", VpcId=vpc)["GroupId"]
        rep.add("net", "Default VPC, subnets, security group (prerequisite)", PASS,
                f"{vpc}, {len(subnets)} subnets, {sg}")
        return vpc, subnets[:2], sg
    except Exception as e:
        rep.add("net", "Default VPC, subnets, security group (prerequisite)", FAIL, err(e))
        return None, [], None


def snapshot(clients, cluster, service, tg_arn=None):
    """One observation of everything a learner could look at."""
    ecs = clients["ecs"]
    snap = {"t": time.time()}
    try:
        svc = ecs.describe_services(cluster=cluster, services=[service])["services"][0]
        primary = next((d for d in svc.get("deployments", []) if d.get("status") == "PRIMARY"), {})
        snap.update(
            running=svc.get("runningCount"), pending=svc.get("pendingCount"),
            desired=svc.get("desiredCount"), deployments=len(svc.get("deployments", [])),
            rollout=primary.get("rolloutState"), rollout_reason=primary.get("rolloutStateReason"),
            failed_tasks=primary.get("failedTasks"),
            task_def=short(svc.get("taskDefinition", "")),
            events=[e.get("message", "") for e in svc.get("events", [])],
        )
    except Exception as e:
        snap["service_error"] = err(e)
    for status in ("RUNNING", "STOPPED"):
        try:
            snap[status.lower() + "_tasks"] = ecs.list_tasks(
                cluster=cluster, serviceName=service, desiredStatus=status).get("taskArns", [])
        except Exception as e:
            snap[status.lower() + "_tasks_error"] = err(e)
    if tg_arn:
        try:
            ths = clients["elbv2"].describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]
            snap["targets"] = [
                {"id": t["Target"].get("Id"), "port": t["Target"].get("Port"),
                 "state": t["TargetHealth"].get("State"), "reason": t["TargetHealth"].get("Reason"),
                 "description": t["TargetHealth"].get("Description")} for t in ths]
        except Exception as e:
            snap["targets_error"] = err(e)
    return snap


def line(snap, t0):
    targets = ",".join(t["state"] or "?" for t in snap.get("targets", [])) or "-"
    return (f"rollout={snap.get('rollout')}  running={snap.get('running')}"
            f"/{snap.get('desired')}  pending={snap.get('pending')}  stopped_tasks="
            f"{len(snap.get('stopped_tasks', []))}  targets=[{targets}]  events={len(snap.get('events', []))}")


def observe(clients, cluster, service, tg_arn, seconds, until=None, every=3):
    """Poll and print a timeline. Stops early if `until(snap)` is true."""
    t0 = time.time()
    timeline, last_line = [], None
    while time.time() - t0 < seconds:
        snap = snapshot(clients, cluster, service, tg_arn)
        timeline.append(snap)
        text = line(snap, t0)
        if text != last_line:  # only print when something changed
            print(f"    +{snap['t'] - t0:4.0f}s  {text}", flush=True)
            last_line = text
        if until and until(snap):
            break
        time.sleep(every)
    return timeline


def describe_all_tasks(clients, cluster, arns):
    out = []
    for i in range(0, len(arns), 100):
        out += clients["ecs"].describe_tasks(cluster=cluster, tasks=arns[i:i + 100]).get("tasks", [])
    return out


def all_task_arns(timeline):
    seen = []
    for s in timeline:
        for a in s.get("running_tasks", []) + s.get("stopped_tasks", []):
            if a not in seen:
                seen.append(a)
    return seen


def stable(snap):
    return (snap.get("rollout") == "COMPLETED" and snap.get("desired") is not None
            and snap.get("running") == snap.get("desired"))


# --------------------------------------------------------------------------- #
# The probes
# --------------------------------------------------------------------------- #

def probe_scenario_one(clients, rep, args, sfx):
    ecs, elbv2 = clients["ecs"], clients["elbv2"]
    cluster, family, service = f"probe-cluster{sfx}", f"probe-checkout-api{sfx}", f"probe-checkout-api{sfx}"
    log_group = f"/ecs/{family}"
    ctx = {"cluster": cluster, "service": service, "log_group": log_group}

    log("Control plane")
    # 1. cluster
    try:
        r = ecs.create_cluster(clusterName=cluster)
        rep.add("cluster", "Create an ECS cluster", PASS,
                f"status={r['cluster'].get('status')} arn={r['cluster'].get('clusterArn')}", strip(r))
    except Exception as e:
        rep.add("cluster", "Create an ECS cluster", FAIL, err(e))
        return ctx

    # 2. task definition (good revision: binds what it declares)
    try:
        try:
            clients["iam"].create_role(RoleName="ecsTaskExecutionRole", AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole",
                                                        "Principal": {"Service": "ecs-tasks.amazonaws.com"}}]}))
        except Exception:
            pass  # exists, or IAM not needed; registration below is the real test
        r = ecs.register_task_definition(**task_def(family, 8080, log_group))
        td = r["taskDefinition"]
        good_td = td["taskDefinitionArn"]
        rep.add("taskdef", "Register a task definition with a portMapping", PASS,
                f"{td['family']}:{td['revision']} status={td.get('status')} "
                f"portMappings={td['containerDefinitions'][0].get('portMappings')}", strip(r))
    except Exception as e:
        rep.add("taskdef", "Register a task definition with a portMapping", FAIL, err(e))
        return ctx

    vpc, subnets, sg = network(clients, rep)
    if not vpc:
        return ctx

    # 3. target group with health check (+ ALB and listener so it is a real attachment)
    try:
        r = elbv2.create_target_group(
            Name=f"probe-tg{sfx}"[:32], Protocol="HTTP", Port=8080, VpcId=vpc, TargetType="ip",
            HealthCheckProtocol="HTTP", HealthCheckPath="/health", HealthCheckPort="traffic-port",
            HealthCheckIntervalSeconds=5, HealthCheckTimeoutSeconds=2,
            HealthyThresholdCount=2, UnhealthyThresholdCount=2, Matcher={"HttpCode": "200"})
        tg = r["TargetGroups"][0]
        tg_arn = ctx["tg_arn"] = tg["TargetGroupArn"]
        rep.add("tg", "Create an ELBv2 target group with a health check", PASS,
                f"{tg.get('HealthCheckProtocol')} {tg.get('HealthCheckPath')} on {tg.get('HealthCheckPort')} "
                f"every {tg.get('HealthCheckIntervalSeconds')}s, matcher={tg.get('Matcher')}", strip(r))
    except Exception as e:
        rep.add("tg", "Create an ELBv2 target group with a health check", FAIL, err(e))
        return ctx
    try:
        lb = elbv2.create_load_balancer(Name=f"probe-alb{sfx}"[:32], Subnets=subnets, SecurityGroups=[sg],
                                        Scheme="internet-facing", Type="application")["LoadBalancers"][0]
        lst = elbv2.create_listener(LoadBalancerArn=lb["LoadBalancerArn"], Protocol="HTTP", Port=18080,
                                    DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}])
        rep.add("alb", "Create an ALB and a listener forwarding to the target group", PASS,
                f"{lb.get('DNSName')} state={lb.get('State', {}).get('Code')}",
                {"loadBalancer": strip(lb), "listener": strip(lst)})
    except Exception as e:
        rep.add("alb", "Create an ALB and a listener forwarding to the target group", FAIL, err(e))
        return ctx

    # 4. service with the load balancer attached, on the good revision
    log("Baseline: a healthy service behind the load balancer")
    svc_kwargs = dict(
        cluster=cluster, serviceName=service, taskDefinition=good_td, desiredCount=2, launchType="FARGATE",
        networkConfiguration={"awsvpcConfiguration": {
            "subnets": subnets, "securityGroups": [sg], "assignPublicIp": "ENABLED"}},
        loadBalancers=[{"targetGroupArn": tg_arn, "containerName": "app", "containerPort": 8080}],
        healthCheckGracePeriodSeconds=0)
    try:
        r = ecs.create_service(**svc_kwargs)
        rep.add("service", "Create an ECS service with a load balancer attached", PASS,
                f"status={r['service'].get('status')} loadBalancers={r['service'].get('loadBalancers')}", strip(r))
    except Exception as e:
        rep.add("service", "Create an ECS service with a load balancer attached", FAIL, err(e))
        return ctx

    def baseline_ok(s):
        ts = s.get("targets", [])
        return stable(s) and len(ts) >= 2 and all(t["state"] == "healthy" for t in ts)

    t0 = time.time()
    base = observe(clients, cluster, service, tg_arn, args.baseline_timeout, until=baseline_ok)
    last = base[-1]
    took = time.time() - t0
    ctx["baseline_seconds"] = took

    rep.add("tasks_run", "Service tasks run as real containers and reach RUNNING",
            PASS if last.get("running") == 2 else FAIL,
            f"runningCount={last.get('running')}/2 after {took:.0f}s, rolloutState={last.get('rollout')}",
            {"final": last})
    ts = last.get("targets", [])
    states = [t["state"] for t in ts]
    if len(ts) >= 2 and all(s == "healthy" for s in states):
        st, note = PASS, f"{len(ts)} targets registered by ECS, all healthy after {took:.0f}s: {ts}"
    elif ts:
        st, note = PARTIAL, f"targets registered but states={states} after {took:.0f}s: {ts}"
    else:
        st, note = FAIL, f"ECS registered no targets in the target group ({last.get('targets_error', 'empty list')})"
    rep.add("alb_healthy", "ALB health check passes against a container that binds the right port", st, note,
            {"targets": ts})
    ev = last.get("events", [])
    rep.add("events_steady", "describe-services event list at steady state",
            PASS if ev else FAIL, f"{len(ev)} event(s): {ev[:3]}", {"events": ev})

    if last.get("running") != 2:
        print("    Baseline never got 2 running tasks. Last Floci log lines:\n" + floci_logs(40))
        return ctx

    # 5. the trap: a revision that binds 3000 while declaring 8080
    log("The trap: revision that binds 3000 while declaring containerPort 8080")
    try:
        r = ecs.register_task_definition(**task_def(family, 3000, log_group))
        bad_td = r["taskDefinition"]["taskDefinitionArn"]
        r2 = ecs.update_service(cluster=cluster, service=service, taskDefinition=bad_td)
        rep.add("mismatch", "Deploy a task whose container binds a different port than declared", PASS,
                f"update-service accepted {short(bad_td)}; "
                f"deployments now={len(r2['service'].get('deployments', []))}", strip(r2))
    except Exception as e:
        rep.add("mismatch", "Deploy a task whose container binds a different port than declared", FAIL, err(e))
        return ctx

    # 6. watch for the failure loop
    print(f"    observing for {args.observe}s ...")
    t_loop = time.time()
    tl = observe(clients, cluster, service, tg_arn, args.observe)
    arns = all_task_arns(tl)
    tasks = describe_all_tasks(clients, cluster, arns) if arns else []
    bad_tasks = [t for t in tasks if t.get("taskDefinitionArn") == bad_td]
    stopped_bad = [t for t in bad_tasks if t.get("lastStatus") == "STOPPED"]
    hc_stops = [t for t in stopped_bad if re.search(r"health", t.get("stoppedReason") or "", re.I)]
    rollouts = [s.get("rollout") for s in tl]
    rollout_seq = [r for i, r in enumerate(rollouts) if i == 0 or r != rollouts[i - 1]]
    target_states = sorted({t["state"] for s in tl for t in s.get("targets", []) if t.get("state")})
    events = []
    for s in tl:
        for m in s.get("events", []):
            if m not in events:
                events.append(m)
    hc_events = [m for m in events if re.search(r"health", m, re.I)]
    final = tl[-1]
    first_completed = next((s["t"] - t_loop for s in tl if stable(s) and s.get("task_def") == short(bad_td)), None)
    ctx["deploy_cycle_seconds"] = first_completed
    loop_ev = {
        "rollout_sequence": rollout_seq, "target_states_seen": target_states,
        "distinct_tasks_on_bad_revision": len(bad_tasks), "stopped_on_bad_revision": len(stopped_bad),
        "stopped_for_health_check": len(hc_stops), "events_seen": events, "final": final,
        "stopped_task_reasons": [{"task": short(t["taskArn"]), "stoppedReason": t.get("stoppedReason"),
                                  "stopCode": t.get("stopCode")} for t in stopped_bad],
        "timeline": tl,
    }

    unhealthy = "unhealthy" in target_states
    rep.add("alb_unhealthy", "ALB health check FAILS against the mismatched container",
            PASS if unhealthy else FAIL,
            f"target states seen during the bad deployment: {target_states or 'none'}; final: "
            f"{[(t['state'], t.get('reason')) for t in final.get('targets', [])]}",
            {"target_states_seen": target_states, "final_targets": final.get("targets")})

    churn = len(bad_tasks) > 2
    if hc_stops and churn and final.get("rollout") != "COMPLETED":
        st = PASS
        note = (f"loop confirmed: {len(hc_stops)} task(s) stopped for failing health checks, "
                f"{len(bad_tasks)} tasks launched on the bad revision, rolloutState {rollout_seq}")
    elif unhealthy and not hc_stops:
        st = FAIL
        note = (f"NO LOOP. The ALB marks targets unhealthy but ECS does not act on it: "
                f"{len(stopped_bad)} bad-revision task(s) stopped, {len(bad_tasks)} launched, "
                f"rolloutState {rollout_seq}, final running={final.get('running')}/{final.get('desired')}")
    else:
        st = FAIL
        note = (f"NO LOOP. targets={target_states or 'none'}, bad-revision tasks launched={len(bad_tasks)} "
                f"stopped={len(stopped_bad)} (for health: {len(hc_stops)}), rolloutState {rollout_seq}")
    rep.add("loop", "ALB health check failure feeds back into ECS (tasks killed, replaced, never stable)",
            st, note, loop_ev)

    if final.get("rollout") == "COMPLETED" and not unhealthy:
        r_st, r_note = FAIL, f"sequence {rollout_seq}"
    elif final.get("rollout") == "COMPLETED":
        r_st = FAIL
        r_note = (f"sequence {rollout_seq}: reaches COMPLETED "
                  f"{'after %.0fs ' % first_completed if first_completed is not None else ''}"
                  f"while targets are unhealthy, so the deployment 'succeeds'")
    else:
        r_st, r_note = PASS, f"sequence {rollout_seq}: stays out of COMPLETED for the whole {args.observe}s window"
    rep.add("rollout", "rolloutState reflects the failing deployment", r_st, r_note, {"sequence": rollout_seq})

    rep.add("events_loop", "Service events describe the failure (started tasks / failed ELB health checks)",
            PASS if hc_events else (PARTIAL if events else FAIL),
            (f"{len(hc_events)} health-check event(s): {hc_events[:2]}" if hc_events else
             f"no event mentions a health check; {len(events)} distinct event(s) seen: {events[:3]}"),
            {"events": events})

    # 7. stopped tasks and stoppedReason
    try:
        stopped_arns = ecs.list_tasks(cluster=cluster, desiredStatus="STOPPED").get("taskArns", [])
        stopped = describe_all_tasks(clients, cluster, stopped_arns) if stopped_arns else []
        reasons = sorted({t.get("stoppedReason") or "<empty>" for t in stopped})
        if not stopped:
            st, note = FAIL, "list-tasks --desired-status STOPPED returned nothing"
        elif any(re.search(r"health", r_, re.I) for r_ in reasons):
            st, note = PASS, f"{len(stopped)} stopped task(s); reasons: {reasons}"
        elif all(r_ != "<empty>" for r_ in reasons):
            st = PARTIAL
            note = (f"{len(stopped)} stopped task(s) with a stoppedReason, but none names a health check: "
                    f"{reasons} (these are the old revision's tasks replaced by the deployment)")
        else:
            st, note = FAIL, f"{len(stopped)} stopped task(s), stoppedReason missing: {reasons}"
        rep.add("stopped", "Stopped tasks are listable and stoppedReason is populated", st, note,
                {"tasks": [{"taskArn": t["taskArn"], "taskDefinitionArn": t.get("taskDefinitionArn"),
                            "lastStatus": t.get("lastStatus"), "stopCode": t.get("stopCode"),
                            "stoppedReason": t.get("stoppedReason"),
                            "containers": [{"name": c.get("name"), "exitCode": c.get("exitCode"),
                                            "reason": c.get("reason")} for c in t.get("containers", [])]}
                           for t in stopped]})
    except Exception as e:
        rep.add("stopped", "Stopped tasks are listable and stoppedReason is populated", FAIL, err(e))

    # 8. container logs through CloudWatch Logs
    probe_logs(clients, rep, log_group, expect="listening on port 3000")
    return ctx


def probe_logs(clients, rep, log_group, expect):
    logs = clients["logs"]
    try:
        streams = logs.describe_log_streams(logGroupName=log_group).get("logStreams", [])
        lines = []
        for s in streams:
            ev = logs.get_log_events(logGroupName=log_group, logStreamName=s["logStreamName"],
                                     startFromHead=True).get("events", [])
            lines += [(s["logStreamName"], e.get("message", "").rstrip()) for e in ev]
        hit = [l for l in lines if expect in l[1]]
        if hit:
            st, note = PASS, f"{len(streams)} stream(s), {len(lines)} line(s); found: {hit[0][0]} -> \"{hit[0][1]}\""
        elif lines:
            st, note = PARTIAL, f"{len(lines)} line(s) reachable but none contains \"{expect}\": {lines[:3]}"
        else:
            st, note = FAIL, f"log group {log_group}: {len(streams)} stream(s), no events"
        rep.add("logs", "Container logs reachable through CloudWatch Logs (awslogs driver)", st, note,
                {"streams": [s["logStreamName"] for s in streams], "sample": lines[:20]})
    except Exception as e:
        rep.add("logs", "Container logs reachable through CloudWatch Logs (awslogs driver)", FAIL, err(e))


def probe_fallback(clients, rep, args, sfx, ctx, key, title, family, port_env, extra_env, health_check, expect):
    """A service with no load balancer, to test one of the PRD's fallback scenarios."""
    ecs = clients["ecs"]
    cluster = ctx["cluster"]
    name = f"{family}{sfx}"
    try:
        _, subnets, sg = ctx["net"]
        td = ecs.register_task_definition(
            **task_def(name, port_env, f"/ecs/{name}", extra_env, health_check))["taskDefinition"]["taskDefinitionArn"]
        ecs.create_service(
            cluster=cluster, serviceName=name, taskDefinition=td, desiredCount=1, launchType="FARGATE",
            networkConfiguration={"awsvpcConfiguration": {
                "subnets": subnets, "securityGroups": [sg], "assignPublicIp": "ENABLED"}})
    except Exception as e:
        rep.add(key, title, FAIL, f"could not set up: {err(e)}")
        return
    print(f"    observing for {args.fallback_observe}s ...")
    tl = observe(clients, cluster, name, None, args.fallback_observe)
    arns = all_task_arns(tl)
    tasks = describe_all_tasks(clients, cluster, arns) if arns else []
    stopped = [t for t in tasks if t.get("lastStatus") == "STOPPED"]
    health = sorted({t.get("healthStatus") or "<none>" for t in tasks})
    reasons = sorted({t.get("stoppedReason") or "<empty>" for t in stopped})
    rollouts = [s.get("rollout") for s in tl]
    seq = [r for i, r in enumerate(rollouts) if i == 0 or r != rollouts[i - 1]]
    events = []
    for s in tl:
        for m in s.get("events", []):
            if m not in events:
                events.append(m)
    facts = (f"tasks launched={len(tasks)}, stopped={len(stopped)}, healthStatus seen={health}, "
             f"stoppedReason={reasons or 'n/a'}, rolloutState {seq}, events={events[:2] or 'none'}")
    looped = len(tasks) >= 3 and len(stopped) >= 2 and tl[-1].get("rollout") != "COMPLETED"
    if expect == "unhealthy_replaced":
        if looped and "UNHEALTHY" in health:
            st = PASS
        elif "UNHEALTHY" in health:
            st, facts = PARTIAL, "healthStatus goes UNHEALTHY but the task is never stopped or replaced. " + facts
        else:
            st = FAIL
    else:  # exit loop
        if looped and all(r != "<empty>" for r in reasons):
            st = PASS
        elif stopped:
            st = PARTIAL
        else:
            st = FAIL
    rep.add(key, title, st, facts, {"timeline": tl, "tasks": [
        {"task": short(t["taskArn"]), "lastStatus": t.get("lastStatus"), "healthStatus": t.get("healthStatus"),
         "stopCode": t.get("stopCode"), "stoppedReason": t.get("stoppedReason"),
         "containers": [{"exitCode": c.get("exitCode"), "healthStatus": c.get("healthStatus"),
                         "reason": c.get("reason")} for c in t.get("containers", [])]} for t in tasks]})


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #

def summary(rep, ctx, args):
    tty = sys.stdout.isatty()
    print("\n" + "=" * 100)
    print("WHAT FLOCI ACTUALLY SUPPORTS")
    print("=" * 100)
    for _, title, status, note in rep.rows:
        c, r = (COLOUR[status], RESET) if tty else ("", "")
        print(f"{c}{status:<8}{r}{title}")
    print("-" * 100)
    if ctx.get("baseline_seconds") is not None and rep.status("alb_healthy") == PASS:
        print(f"Time for a 2-task service to become stable and healthy: {ctx['baseline_seconds']:.0f}s")
    if ctx.get("deploy_cycle_seconds") is not None:
        print(f"Time for a new revision to report COMPLETED:            {ctx['deploy_cycle_seconds']:.0f}s")

    g = rep.status
    print("\nVERDICT")
    if g("loop") == PASS:
        code = 0
        print("  Scenario 1 (ALB health check port mismatch) is VIABLE as written. Continue to build step 2.")
        if g("events_loop") != PASS:
            print("  Caveat: service events do not describe the failure, so 'read the events first' "
                  "cannot be the lesson.")
    elif g("loop") is None:
        code = 1
        print("  INCONCLUSIVE: the probe did not get far enough to test the health check loop. "
              "Fix the first FAIL above and rerun.")
    else:
        code = 2
        print("  Scenario 1 as written is NOT VIABLE on this Floci: the health check failure loop did not")
        print("  happen (see the 'feeds back into ECS' row for exactly what did). Do not build the runner on it.")
        print("  Fallbacks from the PRD, in order:")
        f1, f2 = g("fb_container_hc"), g("fb_exit")
        print(f"    1. Container-level healthCheck variant:  {f1 or 'not tested'}"
              + ("  -> usable" if f1 == PASS else "  -> not a failure loop either" if f1 else ""))
        print(f"    2. Image exits immediately on startup:   {f2 or 'not tested'}"
              + ("  -> usable; this is the scenario to build" if f2 == PASS else ""))
    print("=" * 100)
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--endpoint", default=os.environ.get("ECSLAB_FLOCI_URL", "http://localhost:4566"))
    ap.add_argument("--observe", type=int, default=150, help="seconds to watch the bad deployment (default 150)")
    ap.add_argument("--fallback-observe", type=int, default=75, help="seconds to watch each fallback probe")
    ap.add_argument("--baseline-timeout", type=int, default=180, help="max seconds for the healthy baseline")
    ap.add_argument("--keep", action="store_true", help="leave Floci and the task containers running")
    ap.add_argument("--no-compose", action="store_true", help="do not start/stop Floci; use --endpoint as-is")
    ap.add_argument("--no-build", action="store_true", help="do not rebuild the task image")
    ap.add_argument("--skip-fallbacks", action="store_true", help="only probe scenario 1")
    args = ap.parse_args()

    rep = Report()
    print("ECS Lab preflight probe")
    if not environment(args, rep):
        return 1

    cfg = Config(retries={"max_attempts": 2}, connect_timeout=5, read_timeout=30)
    clients = {n: boto3.client(n, endpoint_url=args.endpoint, region_name=REGION, config=cfg,
                               aws_access_key_id="test", aws_secret_access_key="test")
               for n in ("ecs", "elbv2", "ec2", "logs", "iam")}
    ctx, code = {}, 1
    try:
        if wait_for_floci(clients, rep):
            sfx = "-" + uuid.uuid4().hex[:6] if args.no_compose else ""
            ctx = probe_scenario_one(clients, rep, args, sfx)
            if not args.skip_fallbacks and rep.status("tasks_run") == PASS:
                ctx["net"] = network_quiet(clients)
                log("Fallback 1: container-level healthCheck that fails (binds 3000, check hits 8080)")
                probe_fallback(clients, rep, args, sfx, ctx, "fb_container_hc",
                               "Fallback 1: failing container healthCheck gets the task stopped and replaced",
                               "probe-container-hc", 3000, None,
                               {"command": ["CMD", "/healthcheck", "8080"], "interval": 5, "timeout": 2,
                                "retries": 2, "startPeriod": 0}, "unhealthy_replaced")
                log("Fallback 2: container exits immediately on startup")
                probe_fallback(clients, rep, args, sfx, ctx, "fb_exit",
                               "Fallback 2: crash-on-start loops with a usable stoppedReason",
                               "probe-exit", 8080, [{"name": "EXIT_IMMEDIATELY", "value": "1"}], None, "exit_loop")
        code = summary(rep, ctx, args)
    except KeyboardInterrupt:
        print("\ninterrupted")
    except (BotoCoreError, ClientError, RuntimeError) as e:
        print(f"\nprobe aborted: {err(e)}")
    finally:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out = RESULTS_DIR / f"preflight-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
        out.write_text(json.dumps({
            "endpoint": args.endpoint, "duration_seconds": round(time.time() - rep.started),
            "rows": [dict(zip(("key", "title", "status", "note"), r)) for r in rep.rows],
            "evidence": strip(rep.evidence)}, indent=2, default=str))
        print(f"Full API responses: {out.relative_to(ROOT)}")
        if not args.no_compose:
            (RESULTS_DIR / (out.stem + "-floci.log")).write_text(floci_logs(2000))
            if args.keep:
                print("Floci left running (--keep). Stop it with: docker compose down -v")
            else:
                teardown(clients, ctx)
    return code


def network_quiet(clients):
    ec2 = clients["ec2"]
    vpc = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subnets = [s["SubnetId"] for s in
               ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["Subnets"]][:2]
    sg = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["SecurityGroups"][0]["GroupId"]
    return vpc, subnets, sg


def teardown(clients, ctx):
    """Scale services to zero so Floci removes task containers, then stop Floci."""
    try:
        cluster = ctx.get("cluster")
        if cluster:
            for arn in clients["ecs"].list_services(cluster=cluster).get("serviceArns", []):
                clients["ecs"].update_service(cluster=cluster, service=arn, desiredCount=0)
                clients["ecs"].delete_service(cluster=cluster, service=arn, force=True)
            time.sleep(6)
    except Exception:
        pass
    sh(["docker", "compose", "down", "-v", "--remove-orphans"], check=False)
    leftovers = sh(["docker", "ps", "-aq", "--filter", "network=ecslab"], check=False).split()
    if leftovers:
        sh(["docker", "rm", "-f", *leftovers], check=False)
        sh(["docker", "network", "rm", "ecslab"], check=False)
    print("Floci stopped and task containers removed.")


if __name__ == "__main__":
    sys.exit(main())
