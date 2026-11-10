"""Local web interface for ECS Lab.

Standard library only: an HTTP server for the pages and JSON API, and a small
WebSocket implementation that connects the page's terminal to a real shell.

Security: the terminal is a real shell on this machine, so the server binds to
127.0.0.1 only, requires a random per-run token (cookie), and rejects requests
whose Host or Origin is not this server. Do not expose it on a network.
"""
from __future__ import annotations

import base64
import codecs
import fcntl
import hashlib
import json
import mimetypes
import os
import pty
import re
import secrets
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .. import config, progress, replay, runner

STATIC = Path(__file__).parent / "static"
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
REGISTERABLE = ("family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions", "volumes",
                "placementConstraints", "requiresCompatibilities", "cpu", "memory", "runtimePlatform")


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# AWS access
# --------------------------------------------------------------------------- #

class _Recording:
    """Mock mode has no proxy, so record console clicks in the trail directly."""

    def __init__(self, client, service):
        self._client, self._service = client, service

    def __getattr__(self, op):
        fn = getattr(self._client, op)

        def call(**kw):
            started = time.time()
            status = 200
            result = None
            try:
                result = fn(**kw)
                return result
            except Exception:
                status = 400
                raise
            finally:
                entry = {"ts": round(started, 3), "method": "POST", "path": "/", "service": self._service,
                         "action": "".join(p.capitalize() for p in op.split("_")), "status": status,
                         "source": "console"}
                if op == "list_tasks" and kw.get("desiredStatus") == "STOPPED":
                    entry["detail"] = "stopped"
                if op == "describe_tasks" and any(t.get("lastStatus") == "STOPPED"
                                                  for t in (result or {}).get("tasks", [])):
                    entry["detail"] = "stopped"
                config.STATE_DIR.mkdir(parents=True, exist_ok=True)
                with config.CALL_LOG.open("a") as f:
                    f.write(json.dumps(entry) + "\n")
        return call


def aws(as_learner: bool) -> dict:
    clients = config.clients(as_learner=as_learner)
    if as_learner and config.mock_enabled():
        return {name: _Recording(c, name) for name, c in clients.items()}
    return clients


def _names(scenario):
    seed = scenario["seed_spec"]
    td = seed["task_definitions"][0]
    return {"cluster": seed["cluster"], "service": seed["service"]["name"], "family": td["family"],
            "log_group": td.get("log_group", f"/ecs/{td['family']}"), "target_group": seed["target_group"]["name"],
            "container": td.get("container", "app")}


def _rev(arn):
    return (arn or "").rsplit("/", 1)[-1]


def _chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _task_row(t):
    c = (t.get("containers") or [{}])[0]
    return {"id": t["taskArn"].rsplit("/", 1)[-1], "arn": t["taskArn"],
            "taskDefinition": _rev(t.get("taskDefinitionArn")), "lastStatus": t.get("lastStatus"),
            "desiredStatus": t.get("desiredStatus"), "healthStatus": t.get("healthStatus"),
            "group": t.get("group"), "launchType": t.get("launchType"),
            "createdAt": t.get("createdAt"), "startedAt": t.get("startedAt"), "stoppedAt": t.get("stoppedAt"),
            "stopCode": t.get("stopCode"), "stoppedReason": t.get("stoppedReason"),
            "container": c.get("name"), "exitCode": c.get("exitCode"), "containerReason": c.get("reason")}


def _service_row(s, cluster):
    return {
        "cluster": cluster, "name": s.get("serviceName"), "status": s.get("status"),
        "arn": s.get("serviceArn") or f"arn:aws:ecs:{config.REGION}:{config.ACCOUNT}:service/{cluster}/{s.get('serviceName')}",
        "createdAt": s.get("createdAt"), "schedulingStrategy": s.get("schedulingStrategy") or "REPLICA",
        "network": (s.get("networkConfiguration") or {}).get("awsvpcConfiguration"),
        "taskDefinition": _rev(s.get("taskDefinition")), "desiredCount": s.get("desiredCount"),
        "runningCount": s.get("runningCount"), "pendingCount": s.get("pendingCount"),
        "launchType": s.get("launchType"),
        "healthCheckGracePeriodSeconds": s.get("healthCheckGracePeriodSeconds"),
        "loadBalancers": [{"targetGroup": (lb.get("targetGroupArn") or "").split("/")[-2:-1],
                           "container": lb.get("containerName"), "port": lb.get("containerPort")}
                          for lb in s.get("loadBalancers", [])],
        "deployments": [{"id": d.get("id"), "status": d.get("status"), "taskDefinition": _rev(d.get("taskDefinition")),
                         "rolloutState": d.get("rolloutState"), "desiredCount": d.get("desiredCount"),
                         "runningCount": d.get("runningCount"), "failedTasks": d.get("failedTasks"),
                         "updatedAt": d.get("updatedAt")} for d in s.get("deployments", [])],
        "events": [{"at": e.get("createdAt"), "message": e.get("message")} for e in s.get("events", [])[:100]],
    }


def _services(a, cluster):
    arns = a["ecs"].list_services(cluster=cluster).get("serviceArns", [])
    out = []
    for chunk in _chunks(arns, 10):
        out += a["ecs"].describe_services(cluster=cluster, services=chunk).get("services", [])
    return [s for s in out if s.get("status") != "INACTIVE"]


def _cluster_row(a, c, quiet):
    """`quiet` is the unlogged client: the extra counting calls are the lab's, not the learner's."""
    name = c.get("clusterName") or _rev(c.get("clusterArn"))
    services, running, pending = c.get("activeServicesCount"), c.get("runningTasksCount"), c.get("pendingTasksCount")
    if services is None or running is None:
        svcs = _services(quiet, name)
        services = len(svcs)
        running = len(quiet["ecs"].list_tasks(cluster=name, desiredStatus="RUNNING").get("taskArns", []))
        pending = sum(s.get("pendingCount") or 0 for s in svcs)
    return {"name": name, "arn": c.get("clusterArn") or f"arn:aws:ecs:{config.REGION}:{config.ACCOUNT}:cluster/{name}",
            "status": c.get("status", "ACTIVE"), "services": services, "runningTasks": running,
            "pendingTasks": pending or 0}


def view_clusters(a):
    arns = a["ecs"].list_clusters().get("clusterArns", [])
    clusters = a["ecs"].describe_clusters(clusters=arns).get("clusters", []) if arns else []
    quiet = aws(False)
    return {"clusters": [_cluster_row(a, c, quiet) for c in clusters]}


def view_cluster(a, cluster):
    found = a["ecs"].describe_clusters(clusters=[cluster]).get("clusters", [])
    if not found:
        raise ApiError(f"Cluster {cluster} does not exist.", 404)
    return _cluster_row(a, found[0], aws(False))


def view_services(a, cluster):
    return {"cluster": cluster, "services": [_service_row(s, cluster) for s in _services(a, cluster)]}


def view_service(a, cluster, service):
    found = [s for s in a["ecs"].describe_services(cluster=cluster, services=[service]).get("services", [])
             if s.get("status") != "INACTIVE"]
    if not found:
        return {"missing": True, "cluster": cluster, "name": service}
    return _service_row(found[0], cluster)


def view_tasks(a, cluster, service, status):
    status = "STOPPED" if status == "STOPPED" else "RUNNING"
    kw = {"cluster": cluster, "desiredStatus": status}
    if service:
        kw["serviceName"] = service
    arns = a["ecs"].list_tasks(**kw).get("taskArns", [])
    total = len(arns)
    tasks = a["ecs"].describe_tasks(cluster=cluster, tasks=arns[:100]).get("tasks", []) if arns else []
    rows = [_task_row(t) for t in tasks]
    rows.sort(key=lambda r: str(r.get("stoppedAt") or r.get("startedAt") or r.get("createdAt") or ""), reverse=True)
    return {"status": status, "total": total, "more": total >= 100, "tasks": rows}


def _log_location(a, task_def_arn, container=None):
    """Where a task's container logs go, read from its task definition."""
    try:
        td = a["ecs"].describe_task_definition(taskDefinition=task_def_arn)["taskDefinition"]
        defs = td["containerDefinitions"]
        c = next((d for d in defs if d.get("name") == container), defs[0])
        opts = (c.get("logConfiguration") or {}).get("options") or {}
        return opts.get("awslogs-group"), opts.get("awslogs-stream-prefix"), c.get("name")
    except Exception:
        return None, None, container


def view_task(a, cluster, task_id):
    tasks = a["ecs"].describe_tasks(cluster=cluster, tasks=[task_id]).get("tasks", [])
    if not tasks:
        raise ApiError(f"Task {task_id} was not found. Stopped tasks are only kept for a while.", 404)
    t = tasks[0]
    row = _task_row(t)
    group, prefix, cname = _log_location(aws(False), t.get("taskDefinitionArn"), row.get("container"))
    row.update({
        "cluster": cluster, "cpu": t.get("cpu"), "memory": t.get("memory"),
        "platformVersion": t.get("platformVersion"), "logGroup": group,
        "logStream": f"{prefix}/{cname}/{row['id']}" if group and prefix else None,
        "containers": [{"name": c.get("name"), "image": c.get("image"), "lastStatus": c.get("lastStatus"),
                        "healthStatus": c.get("healthStatus"), "exitCode": c.get("exitCode"),
                        "reason": c.get("reason")} for c in t.get("containers", [])]})
    return row


def view_service_logs(a, cluster, service):
    found = aws(False)["ecs"].describe_services(cluster=cluster, services=[service]).get("services", [])
    group = _log_location(aws(False), found[0].get("taskDefinition"))[0] if found else None
    if not group:
        return {"group": None, "events": [], "error": "This service's task definition has no awslogs log group."}
    try:
        events = a["logs"].filter_log_events(logGroupName=group, limit=500).get("events", [])
    except Exception as e:
        return {"group": group, "events": [], "error": _message(e)}
    events.sort(key=lambda e: e.get("timestamp") or 0, reverse=True)
    return {"group": group,
            "events": [{"at": e.get("timestamp"), "message": (e.get("message") or "").rstrip(),
                        "stream": e.get("logStreamName"),
                        "task": (e.get("logStreamName") or "").rsplit("/", 1)[-1]} for e in events[:500]]}


def view_families(a):
    try:
        families = a["ecs"].list_task_definition_families(status="ACTIVE").get("families", [])
    except Exception:
        arns = a["ecs"].list_task_definitions().get("taskDefinitionArns", [])
        families = sorted({_rev(x).rsplit(":", 1)[0] for x in arns})
    return {"families": families}


def view_taskdefs(a, family):
    arns = a["ecs"].list_task_definitions(familyPrefix=family, sort="DESC").get("taskDefinitionArns", [])
    arns = [x for x in arns if f":task-definition/{family}:" in x]
    arns.sort(key=lambda x: int(x.rsplit(":", 1)[-1]), reverse=True)
    return {"family": family, "revisions": [_rev(x) for x in arns]}


def view_taskdef(a, name):
    td = a["ecs"].describe_task_definition(taskDefinition=name)["taskDefinition"]
    return {"name": _rev(td["taskDefinitionArn"]), "revision": td.get("revision"), "status": td.get("status"),
            "arn": td.get("taskDefinitionArn"),
            "containers": [{"name": c.get("name"), "image": c.get("image"),
                            "portMappings": c.get("portMappings", []),
                            "environment": c.get("environment", []),
                            "logConfiguration": c.get("logConfiguration")} for c in td["containerDefinitions"]],
            "registerable": {k: td[k] for k in REGISTERABLE if k in td}}


def _target_group(a, tg, with_targets=True):
    health = a["elbv2"].describe_target_health(TargetGroupArn=tg["TargetGroupArn"])["TargetHealthDescriptions"]
    out = {"name": tg.get("TargetGroupName"), "protocol": tg.get("Protocol"), "port": tg.get("Port"),
           "arn": tg.get("TargetGroupArn"), "targetType": tg.get("TargetType") or "ip", "vpc": tg.get("VpcId"),
           "loadBalancers": [_rev(x) if "/" not in x else x.split("/")[-2] for x in tg.get("LoadBalancerArns", [])],
           "healthy": sum(1 for t in health if t["TargetHealth"].get("State") == "healthy"), "total": len(health)}
    if with_targets:
        out["healthCheck"] = {
            "enabled": tg.get("HealthCheckEnabled", True), "protocol": tg.get("HealthCheckProtocol"),
            "path": tg.get("HealthCheckPath"), "port": tg.get("HealthCheckPort"),
            "intervalSeconds": tg.get("HealthCheckIntervalSeconds"), "timeoutSeconds": tg.get("HealthCheckTimeoutSeconds"),
            "healthyThreshold": tg.get("HealthyThresholdCount"), "unhealthyThreshold": tg.get("UnhealthyThresholdCount"),
            "matcher": (tg.get("Matcher") or {}).get("HttpCode")}
        out["targets"] = [{"id": t["Target"].get("Id"), "port": t["Target"].get("Port"),
                           "state": t["TargetHealth"].get("State"), "reason": t["TargetHealth"].get("Reason"),
                           "description": t["TargetHealth"].get("Description")} for t in health]
    return out


def view_tgs(a):
    groups = a["elbv2"].describe_target_groups().get("TargetGroups", [])
    quiet = aws(False)
    return {"targetGroups": [_target_group(quiet, tg, with_targets=False) for tg in groups]}


def view_targets(a, name):
    try:
        found = a["elbv2"].describe_target_groups(Names=[name]).get("TargetGroups", [])
    except Exception:
        found = []
    if not found:
        return {"missing": True, "name": name}
    return _target_group(a, found[0])


def view_log_groups(a):
    groups = a["logs"].describe_log_groups().get("logGroups", [])
    return {"groups": [{"name": g.get("logGroupName"), "retention": g.get("retentionInDays"),
                        "createdAt": g.get("creationTime"), "storedBytes": g.get("storedBytes")} for g in groups]}


def do_modify_health_check(a, name, body):
    try:
        found = aws(False)["elbv2"].describe_target_groups(Names=[name]).get("TargetGroups", [])
    except Exception:
        found = []
    if not found:
        raise ApiError(f"Target group {name} does not exist.", 404)
    kw = {}
    if body.get("path") is not None:
        path = str(body["path"]).strip()
        if not path.startswith("/"):
            raise ApiError("The health check path must start with /")
        kw["HealthCheckPath"] = path
    if body.get("matcher") is not None:
        if not re.fullmatch(r"[0-9,\- ]{3,40}", str(body["matcher"])):
            raise ApiError("Success codes are HTTP codes such as 200, 200-299 or 200,202")
        kw["Matcher"] = {"HttpCode": str(body["matcher"]).replace(" ", "")}
    for key, field, lo, hi in (("interval", "HealthCheckIntervalSeconds", 5, 300), ("timeout", "HealthCheckTimeoutSeconds", 2, 120),
                               ("healthy", "HealthyThresholdCount", 2, 10), ("unhealthy", "UnhealthyThresholdCount", 2, 10)):
        if body.get(key) not in (None, ""):
            try:
                v = int(body[key])
            except (TypeError, ValueError):
                raise ApiError(f"{key} must be a whole number")
            if not lo <= v <= hi:
                raise ApiError(f"{key} must be between {lo} and {hi}")
            kw[field] = v
    if not kw:
        raise ApiError("Nothing to change")
    a["elbv2"].modify_target_group(TargetGroupArn=found[0]["TargetGroupArn"], **kw)
    return view_targets(aws(False), name)


def view_namespaces(a):
    sd = a["servicediscovery"]
    out = []
    for ns in sd.list_namespaces().get("Namespaces", []):
        services = sd.list_services(Filters=[{"Name": "NAMESPACE_ID", "Values": [ns["Id"]]}]).get("Services", [])
        for s in services:
            sid = s.get("Id") or (s.get("Arn") or "").rsplit("/", 1)[-1]
            try:
                instances = sd.list_instances(ServiceId=sid).get("Instances", [])
            except Exception:
                instances = []
            out.append({"namespace": ns["Name"], "service": s["Name"], "dns": f"{s['Name']}.{ns['Name']}",
                        "instances": [{"id": i.get("Id"), "ip": (i.get("Attributes") or {}).get("AWS_INSTANCE_IPV4"),
                                       "port": (i.get("Attributes") or {}).get("AWS_INSTANCE_PORT")} for i in instances]})
        if not services:
            out.append({"namespace": ns["Name"], "service": None, "dns": None, "instances": []})
    return {"records": out}


def _rule_rows(sg):
    rows = []
    for p in sg.get("IpPermissions", []):
        proto = str(p.get("IpProtocol"))
        ports = "All" if proto == "-1" else (str(p.get("FromPort")) if p.get("FromPort") == p.get("ToPort")
                                             else f"{p.get('FromPort')} - {p.get('ToPort')}")
        sources = [r.get("CidrIp") for r in p.get("IpRanges", [])] + [g.get("GroupId") for g in p.get("UserIdGroupPairs", [])]
        for src in sources or ["-"]:
            rows.append({"protocol": "All" if proto == "-1" else proto.upper(), "ports": ports, "source": src,
                         "fromPort": p.get("FromPort"), "toPort": p.get("ToPort"), "ipProtocol": proto})
    return rows


def _sg_row(sg):
    return {"id": sg.get("GroupId"), "name": sg.get("GroupName"), "description": sg.get("Description"),
            "vpc": sg.get("VpcId"), "inbound": _rule_rows(sg)}


def view_security_groups(a):
    return {"groups": [_sg_row(g) for g in a["ec2"].describe_security_groups().get("SecurityGroups", [])]}


def view_security_group(a, group_id):
    found = a["ec2"].describe_security_groups(GroupIds=[group_id]).get("SecurityGroups", [])
    if not found:
        raise ApiError(f"Security group {group_id} does not exist.", 404)
    return _sg_row(found[0])


def do_set_ingress(a, group_id, rules):
    from .. import seeder
    clean = []
    for r in rules or []:
        try:
            lo, hi = int(r.get("from_port")), int(r.get("to_port", r.get("from_port")))
        except (TypeError, ValueError):
            raise ApiError("Each rule needs a port, or a port range, in numbers")
        if not (0 <= lo <= hi <= 65535):
            raise ApiError(f"Port range {lo} - {hi} is not valid")
        cidr = str(r.get("cidr") or "").strip()
        if "/" not in cidr:
            raise ApiError(f"Source '{cidr}' is not a CIDR block such as 0.0.0.0/0")
        clean.append({"protocol": "tcp", "from_port": lo, "to_port": hi, "cidr": cidr})
    seeder.set_ingress(a, group_id, clean)
    return view_security_group(aws(False), group_id)


def _policy_doc(doc):
    import urllib.parse
    return json.loads(urllib.parse.unquote(doc)) if isinstance(doc, str) else doc


def view_roles(a):
    roles = a["iam"].list_roles().get("Roles", [])
    return {"roles": [{"name": r.get("RoleName"), "arn": r.get("Arn"), "createdAt": r.get("CreateDate")} for r in roles]}


def view_role(a, name):
    iam = a["iam"]
    try:
        role = iam.get_role(RoleName=name)["Role"]
    except Exception:
        raise ApiError(f"Role {name} does not exist.", 404)
    inline = []
    for pname in iam.list_role_policies(RoleName=name).get("PolicyNames", []):
        inline.append({"name": pname, "type": "Customer inline",
                       "document": _policy_doc(iam.get_role_policy(RoleName=name, PolicyName=pname)["PolicyDocument"])})
    attached = [{"name": p.get("PolicyName"), "type": "AWS managed", "arn": p.get("PolicyArn")}
                for p in iam.list_attached_role_policies(RoleName=name).get("AttachedPolicies", [])]
    return {"name": role.get("RoleName"), "arn": role.get("Arn"), "createdAt": role.get("CreateDate"),
            "trust": _policy_doc(role.get("AssumeRolePolicyDocument") or {}), "policies": inline + attached}


def view_secrets(a):
    secrets = a["secretsmanager"].list_secrets().get("SecretList", [])
    return {"secrets": [{"name": s.get("Name"), "arn": s.get("ARN"), "changedAt": s.get("LastChangedDate")} for s in secrets]}


def view_secret(a, name, reveal):
    sm = a["secretsmanager"]
    try:
        d = sm.describe_secret(SecretId=name)
    except Exception:
        raise ApiError(f"Secret {name} does not exist.", 404)
    out = {"name": d.get("Name"), "arn": d.get("ARN"), "changedAt": d.get("LastChangedDate"), "value": None, "pairs": None}
    if reveal:      # a separate call, as on AWS: reading the value is its own action
        value = sm.get_secret_value(SecretId=name).get("SecretString")
        out["value"] = value
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                out["pairs"] = [{"key": k, "value": str(v)} for k, v in parsed.items()]
        except (TypeError, ValueError):
            pass
    return out


def view_parameters(a):
    params = a["ssm"].describe_parameters().get("Parameters", [])
    return {"parameters": [{"name": p.get("Name"), "type": p.get("Type"), "changedAt": p.get("LastModifiedDate")} for p in params]}


def view_parameter(a, name, reveal):
    try:
        p = a["ssm"].get_parameter(Name=name, WithDecryption=bool(reveal))["Parameter"]
    except Exception:
        raise ApiError(f"Parameter {name} does not exist.", 404)
    return {"name": p.get("Name"), "type": p.get("Type"), "arn": p.get("ARN"), "changedAt": p.get("LastModifiedDate"),
            "value": p.get("Value") if reveal or p.get("Type") != "SecureString" else None}


def view_log_streams(a, group):
    try:
        streams = a["logs"].describe_log_streams(logGroupName=group, orderBy="LastEventTime",
                                                 descending=True, limit=50).get("logStreams", [])
    except Exception as e:
        return {"group": group, "streams": [], "error": _message(e)}
    streams.sort(key=lambda s: s.get("lastEventTimestamp") or s.get("creationTime") or 0, reverse=True)
    return {"group": group,
            "streams": [{"name": s["logStreamName"], "task": s["logStreamName"].rsplit("/", 1)[-1],
                         "lastEvent": s.get("lastEventTimestamp") or s.get("creationTime")} for s in streams]}


def view_log_events(a, group, stream):
    events = a["logs"].get_log_events(logGroupName=group, logStreamName=stream,
                                      startFromHead=True, limit=1000).get("events", [])
    return {"group": group, "stream": stream,
            "events": [{"at": e.get("timestamp"), "message": (e.get("message") or "").rstrip()} for e in events]}


def do_stop_task(a, cluster, task_id, reason):
    t = a["ecs"].stop_task(cluster=cluster, task=task_id, reason=reason or "Task stopped by user").get("task", {})
    return {"id": task_id, "lastStatus": t.get("lastStatus"), "desiredStatus": t.get("desiredStatus")}


def do_run_task(a, cluster, task_definition, count):
    if not 1 <= count <= 10:
        raise ApiError("Desired tasks must be between 1 and 10")
    quiet = aws(False)
    network = next((s.get("networkConfiguration") for s in _services(quiet, cluster) if s.get("networkConfiguration")), None)
    if not network:
        from .. import seeder
        _, subnets, sg = seeder._network(quiet)
        network = {"awsvpcConfiguration": {"subnets": subnets, "securityGroups": [sg], "assignPublicIp": "ENABLED"}}
    r = a["ecs"].run_task(cluster=cluster, taskDefinition=task_definition, count=count, launchType="FARGATE",
                          networkConfiguration=network)
    return {"tasks": [t["taskArn"].rsplit("/", 1)[-1] for t in r.get("tasks", [])],
            "failures": [f.get("reason") for f in r.get("failures", [])]}


def _message(e):
    resp = getattr(e, "response", None)
    if resp:
        return f"{resp['Error'].get('Code')}: {resp['Error'].get('Message')}"
    return str(e) or type(e).__name__


# --------------------------------------------------------------------------- #
# Lab state
# --------------------------------------------------------------------------- #

_lab_lock = threading.Lock()   # run / check / reset are not safe to interleave


def lab_state():
    state = runner.load_state()
    out = {"scenarios": [], "active": None, "mock": config.mock_enabled(), "proxy": config.PROXY_URL,
           "now": time.time()}
    done = progress.load()
    for sid in runner.available():
        s = runner.load(sid)
        mine = done.get(sid, {})
        kind = "task" if s.get("selftest", {}).get("task") else "incident" if s["seed_spec"].get("incident") else "deploy"
        out["scenarios"].append({"id": sid, "title": s["title"], "difficulty": s["difficulty"],
                                 "minutes": s["estimated_minutes"], "topic": s.get("topic", "Other"), "kind": kind,
                                 "solved": bool(mine.get("solved")), "bestSeconds": mine.get("best_seconds"),
                                 "attempts": len(mine.get("attempts", []))})
    if state:
        s = runner.load(state["scenario"])
        hints = s.get("hints", [])
        shown = state.get("hints_shown", 0)
        out["active"] = {
            "id": s["id"], "title": s["title"], "brief": s["brief"], "difficulty": s["difficulty"],
            "minutes": s["estimated_minutes"], "startedAt": state["started_at"], "solvedAt": state.get("solved_at"),
            "hints": [h["text"] for h in hints[:shown]], "hintsTotal": len(hints),
            "nextHintAt": (state["started_at"] + hints[shown]["after_minutes"] * 60) if shown < len(hints) else None,
            "names": _names(s), "onAws": s.get("on_aws", []) if state.get("solved_at") else []}
    return out


def _active():
    state = runner.load_state()
    if not state:
        raise ApiError("No scenario is running. Start one first.", 409)
    return state, runner.load(state["scenario"])


def lab_run(scenario_id):
    with _lab_lock:
        try:
            scenario = runner.load(scenario_id)
        except FileNotFoundError as e:
            raise ApiError(str(e), 404)
        try:
            from .. import emulator
            settings = scenario["seed_spec"].get("emulator")
            if runner.load_state():
                emulator.fresh(settings)      # something was already running: start from a clean emulator
            else:
                emulator.ensure(settings)     # restart Floci only if this scenario needs other settings
            for f in (config.MOCK_STATE,):
                f.unlink(missing_ok=True)
            runner.start(config.clients(), scenario)
        except Exception as e:
            raise ApiError(f"Could not set up the scenario: {_message(e)}. Is the emulator running? "
                           f"Try `ecslab start` in a terminal.", 502)
    return lab_state()


def lab_check():
    with _lab_lock:
        state, scenario = _active()
        settle = 0 if config.mock_enabled() else 8
        deadline = time.time() + (0 if config.mock_enabled() else 30)
        while True:
            outcome = runner.check(config.clients(), scenario, settle_seconds=settle)
            if outcome.status != runner.FAILING or time.time() >= deadline:
                break
            time.sleep(4)
        result = {"status": outcome.status, "messages": outcome.messages,
                  "details": [{"rule": r, "ok": ok, "reason": why} for r, ok, why in outcome.details]}
        if outcome.status == runner.PASSED:
            first = not state.get("solved_at")
            if first:
                state["solved_at"] = time.time()
                runner.save_state(state)
            calls = runner.read_calls()
            result["replay"] = replay.build(calls, scenario.get("replay"), state["started_at"], state["solved_at"])
            result["analysis"] = replay.analyse(calls, scenario.get("replay"), state["started_at"], state["solved_at"])
            result["teaches"] = scenario.get("teaches", [])
            result["onAws"] = scenario.get("on_aws", [])
            if first:
                progress.record_pass(scenario["id"], state["solved_at"] - state["started_at"], state.get("hints_shown", 0),
                                     result["analysis"]["calls"], result["replay"])
        return result


def lab_hint():
    state, scenario = _active()
    return {"message": runner.next_hint(scenario, state), **lab_state()}


def lab_reset():
    with _lab_lock:
        for f in (config.STATE_FILE, config.CALL_LOG, config.MOCK_STATE):
            f.unlink(missing_ok=True)
        from .. import emulator
        emulator.fresh()
    return lab_state()


def trail(since: float = 0):
    state = runner.load_state()
    start = state["started_at"] if state else 0
    calls = [c for c in runner.read_calls() if c.get("ts", 0) >= start and c.get("ts", 0) > since]
    return {"startedAt": start, "calls": calls[-300:]}


# --------------------------------------------------------------------------- #
# Terminal over WebSocket
# --------------------------------------------------------------------------- #

def ws_send(wfile, lock, payload: bytes, opcode: int = 1):
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    with lock:
        wfile.write(head + payload)
        wfile.flush()


def ws_read(rfile):
    """Return (opcode, payload) for one frame, or (None, b'') on EOF."""
    head = rfile.read(2)
    if len(head) < 2:
        return None, b""
    opcode, n = head[0] & 0x0F, head[1] & 0x7F
    masked = head[1] & 0x80
    if n == 126:
        n = struct.unpack("!H", rfile.read(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", rfile.read(8))[0]
    if n > 1 << 20:
        return None, b""
    mask = rfile.read(4) if masked else b""
    data = rfile.read(n)
    if masked:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return opcode, data


_PTY_HELPER = """
import fcntl, os, sys, termios
os.setsid()
fd = int(sys.argv[1])
try:
    fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
except OSError:
    pass
for i in (0, 1, 2):
    os.dup2(fd, i)
if fd > 2:
    os.close(fd)
os.execvp(sys.argv[2], [sys.argv[2], "-i"])
"""


def terminal_session(handler):
    """Bridge one WebSocket connection to a shell that is already pointed at the lab."""
    key = handler.headers.get("Sec-WebSocket-Key", "")
    accept = base64.b64encode(hashlib.sha1((key + WS_MAGIC).encode()).digest()).decode()
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept)
    handler.end_headers()
    handler.close_connection = True

    env = dict(os.environ)
    env.update(config.learner_env())
    env.update({"TERM": "xterm-256color", "ECSLAB_TERMINAL": "1"})
    shell = env.get("SHELL") or "/bin/bash"
    workdir = config.STATE_DIR / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)

    # The shell is started through a small single-threaded helper that makes the
    # pty its controlling terminal. Forking directly from this threaded server
    # is unsafe (and deprecated in recent Pythons).
    fd, slave = pty.openpty()
    try:
        proc = subprocess.Popen([sys.executable, "-c", _PTY_HELPER, str(slave), shell], env=env,
                                cwd=workdir, stdin=subprocess.DEVNULL, pass_fds=[slave])
    finally:
        os.close(slave)

    lock = threading.Lock()
    alive = threading.Event()
    alive.set()

    def pump_output():
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while alive.is_set():
                ready, _, _ = select.select([fd], [], [], 0.5)
                if not ready:
                    continue
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    ws_send(handler.wfile, lock, text.encode("utf-8"))
        except (OSError, ValueError):     # the browser went away mid-write
            pass
        finally:
            alive.clear()
            try:
                ws_send(handler.wfile, lock, b"", opcode=8)
            except (OSError, ValueError):
                pass

    threading.Thread(target=pump_output, daemon=True).start()
    try:
        while alive.is_set():
            opcode, data = ws_read(handler.rfile)
            if opcode is None or opcode == 8:
                break
            if opcode == 9:
                ws_send(handler.wfile, lock, data, opcode=10)
                continue
            if opcode != 1:
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except ValueError:
                continue
            if msg.get("type") == "input":
                os.write(fd, msg.get("data", "").encode("utf-8"))
            elif msg.get("type") == "resize":
                rows, cols = max(2, int(msg.get("rows", 24))), max(10, int(msg.get("cols", 80)))
                fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except (OSError, ValueError):
        pass
    finally:
        alive.clear()
        for sig in (signal.SIGHUP, signal.SIGKILL):
            if proc.poll() is not None:
                break
            try:
                os.killpg(proc.pid, sig)     # the shell leads its own session
            except OSError:
                break
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        try:
            os.close(fd)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def make_handler(token: str, port: int):
    allowed_hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # ---- plumbing ---- #
        def log_message(self, *_):
            pass

        def _send(self, status, body: bytes, ctype="application/json", extra=()):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status=200):
            self._send(status, json.dumps(obj, default=str).encode())

        def _authorised(self):
            cookies = dict(p.strip().split("=", 1) for p in (self.headers.get("Cookie") or "").split(";") if "=" in p)
            return secrets.compare_digest(cookies.get("ecslab_token", ""), token)

        def _trusted_request(self):
            """Blocks other websites (and DNS rebinding) from driving this server."""
            if self.headers.get("Host") not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in allowed_origins

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1 << 20:
                raise ApiError("Request too large", 413)
            try:
                return json.loads(self.rfile.read(n) or b"{}") if n else {}
            except ValueError:
                raise ApiError("Body must be JSON")

        # ---- routes ---- #
        def do_GET(self):
            url = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if not self._trusted_request():
                return self._send(403, b"Forbidden", "text/plain")

            if url.path == "/" and "token" in q:   # first visit: trade the token for a cookie
                if not secrets.compare_digest(q["token"], token):
                    return self._send(403, b"Wrong or expired link. Run `ecslab ui` again.", "text/plain")
                return self._send(302, b"", "text/plain", [
                    ("Location", "/"),
                    ("Set-Cookie", f"ecslab_token={token}; Path=/; HttpOnly; SameSite=Strict")])
            if not self._authorised():
                return self._send(403, b"Open the link printed by `ecslab ui`.", "text/plain")

            if url.path == "/ws/terminal":
                if (self.headers.get("Upgrade") or "").lower() != "websocket" or not self.headers.get("Origin"):
                    return self._send(400, b"WebSocket only", "text/plain")
                return terminal_session(self)
            if url.path.startswith("/api/"):
                return self._api("GET", url.path, q, {})
            return self._static(url.path)

        def do_POST(self):
            url = urlsplit(self.path)
            if not self._trusted_request() or not self._authorised() or not self.headers.get("Origin"):
                return self._send(403, b"Forbidden", "text/plain")
            try:
                body = self._body()
            except ApiError as e:
                return self._json({"error": str(e)}, e.status)
            return self._api("POST", url.path, {}, body)

        def _static(self, path):
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            file = (STATIC / rel).resolve()
            if STATIC.resolve() not in file.parents or not file.is_file():
                return self._send(404, b"Not found", "text/plain")
            ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            self._send(200, file.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text/") or
                                                         ctype.endswith("javascript") else ""))

        def _api(self, method, path, q, body):
            try:
                self._json(self._route(method, path, q, body))
            except ApiError as e:
                self._json({"error": str(e)}, e.status)
            except Exception as e:  # AWS errors and anything unexpected
                if not getattr(e, "response", None):
                    traceback.print_exc()
                self._json({"error": _message(e)}, 502 if not getattr(e, "response", None) else 400)

        def _route(self, method, path, q, body):
            key = (method, path)
            if key == ("GET", "/api/state"):
                return lab_state()
            if key == ("GET", "/api/trail"):
                return trail(float(q.get("since", 0)))
            if key == ("POST", "/api/run"):
                return lab_run(body.get("scenario", ""))
            if key == ("POST", "/api/check"):
                return lab_check()
            if key == ("POST", "/api/hint"):
                return lab_hint()
            if key == ("POST", "/api/reset"):
                return lab_reset()
            if key == ("GET", "/api/solution"):
                state, scenario = _active()
                if not state.get("solved_at"):
                    raise ApiError("The reference solution unlocks once you have solved the scenario.", 403)
                from .. import solution
                return {"markdown": solution.describe(scenario["id"])}
            if key == ("GET", "/api/progress"):
                mine = progress.load().get(q.get("scenario", ""), {})
                return {"scenario": q.get("scenario"), "solved": bool(mine.get("solved")), "bestSeconds": mine.get("best_seconds"),
                        "attempts": list(reversed(mine.get("attempts", [])))}

            _, scenario = _active()
            n = _names(scenario)
            p = {**q, **{k: v for k, v in body.items() if isinstance(v, (str, int, bool))}}
            cluster, service = str(p.get("cluster") or n["cluster"]), p.get("service")
            # A click is the learner looking at something, so it goes through the
            # proxy and lands in the trail. Background refreshes do not.
            a = aws(as_learner=q.get("user") == "1" or method == "POST")
            if key == ("GET", "/api/aws/clusters"):
                return view_clusters(a)
            if key == ("GET", "/api/aws/cluster"):
                return view_cluster(a, cluster)
            if key == ("GET", "/api/aws/services"):
                return view_services(a, cluster)
            if key == ("GET", "/api/aws/service"):
                return view_service(a, cluster, str(service or n["service"]))
            if key == ("GET", "/api/aws/tasks"):
                return view_tasks(a, cluster, str(service) if service else None, q.get("status", "RUNNING"))
            if key == ("GET", "/api/aws/task"):
                return view_task(a, cluster, q.get("id", ""))
            if key == ("GET", "/api/aws/service-logs"):
                return view_service_logs(a, cluster, str(service or n["service"]))
            if key == ("GET", "/api/aws/families"):
                return view_families(a)
            if key == ("GET", "/api/aws/taskdefs"):
                return view_taskdefs(a, q.get("family") or n["family"])
            if key == ("GET", "/api/aws/taskdef"):
                return view_taskdef(a, q.get("name") or n["family"])
            if key == ("GET", "/api/aws/tgs"):
                return view_tgs(a)
            if key == ("GET", "/api/aws/targets"):
                return view_targets(a, q.get("name") or n["target_group"])
            if key == ("GET", "/api/aws/loggroups"):
                return view_log_groups(a)
            if key == ("GET", "/api/aws/logs"):
                group = q.get("group") or n["log_group"]
                return view_log_events(a, group, q["stream"]) if q.get("stream") else view_log_streams(a, group)
            if key == ("POST", "/api/aws/tg-health"):
                return do_modify_health_check(a, str(body.get("name") or n["target_group"]), body)
            if key == ("GET", "/api/aws/namespaces"):
                return view_namespaces(a)
            if key == ("GET", "/api/aws/buckets"):
                return {"buckets": [{"name": b.get("Name"), "createdAt": b.get("CreationDate")}
                                    for b in a["s3"].list_buckets().get("Buckets", [])]}
            if key == ("GET", "/api/aws/sgs"):
                return view_security_groups(a)
            if key == ("GET", "/api/aws/sg"):
                return view_security_group(a, q.get("id", ""))
            if key == ("POST", "/api/aws/sg-rules"):
                return do_set_ingress(a, str(body.get("id") or ""), body.get("rules"))
            if key == ("GET", "/api/aws/roles"):
                return view_roles(a)
            if key == ("GET", "/api/aws/role"):
                return view_role(a, q.get("name", ""))
            if key == ("POST", "/api/aws/role-policy"):
                doc = body.get("document")
                if not isinstance(doc, dict) or "Statement" not in doc:
                    raise ApiError("A policy is a JSON object with a Statement")
                if not re.fullmatch(r"[\w+=,.@-]{1,128}", str(body.get("name") or "")):
                    raise ApiError("Give the policy a name using letters, digits and + = , . @ - _")
                a["iam"].put_role_policy(RoleName=str(body.get("role")), PolicyName=str(body["name"]),
                                         PolicyDocument=json.dumps(doc))
                return view_role(aws(False), str(body.get("role")))
            if key == ("POST", "/api/aws/role-policy-delete"):
                a["iam"].delete_role_policy(RoleName=str(body.get("role")), PolicyName=str(body.get("name")))
                return view_role(aws(False), str(body.get("role")))
            if key == ("GET", "/api/aws/secrets"):
                return view_secrets(a)
            if key == ("GET", "/api/aws/secret"):
                return view_secret(a, q.get("name", ""), q.get("reveal") == "1")
            if key == ("GET", "/api/aws/parameters"):
                return view_parameters(a)
            if key == ("GET", "/api/aws/parameter"):
                return view_parameter(a, q.get("name", ""), q.get("reveal") == "1")
            if key == ("POST", "/api/aws/update-service"):
                kw = {}
                if body.get("taskDefinition"):
                    kw["taskDefinition"] = str(body["taskDefinition"])
                if body.get("desiredCount") is not None:
                    kw["desiredCount"] = int(body["desiredCount"])
                if body.get("forceNewDeployment"):
                    kw["forceNewDeployment"] = True
                groups = body.get("securityGroups")
                if isinstance(groups, list) and groups:      # keep the subnets, change only the groups
                    cur = aws(False)["ecs"].describe_services(cluster=cluster, services=[str(service or n["service"])])["services"][0]
                    vpc = dict((cur.get("networkConfiguration") or {}).get("awsvpcConfiguration") or {})
                    if sorted(vpc.get("securityGroups") or []) != sorted(map(str, groups)):
                        vpc["securityGroups"] = [str(g) for g in groups]
                        kw["networkConfiguration"] = {"awsvpcConfiguration": vpc}
                if not kw:
                    raise ApiError("Nothing to change")
                name = str(service or n["service"])
                a["ecs"].update_service(cluster=cluster, service=name, **kw)
                return view_service(aws(False), cluster, name)
            if key == ("POST", "/api/aws/stop-task"):
                if not body.get("id"):
                    raise ApiError("Choose a task to stop")
                return do_stop_task(a, cluster, str(body["id"]), str(body.get("reason") or ""))
            if key == ("POST", "/api/aws/run-task"):
                if not body.get("taskDefinition"):
                    raise ApiError("Choose a task definition")
                return do_run_task(a, cluster, str(body["taskDefinition"]), int(body.get("count") or 1))
            if key == ("POST", "/api/aws/register-taskdef"):
                definition = body.get("definition")
                if not isinstance(definition, dict) or not definition.get("containerDefinitions"):
                    raise ApiError("The task definition must be a JSON object with containerDefinitions")
                definition = {k: v for k, v in definition.items() if k in REGISTERABLE}
                definition.setdefault("family", n["family"])
                td = a["ecs"].register_task_definition(**definition)["taskDefinition"]
                return {"name": _rev(td["taskDefinitionArn"])}
            raise ApiError("Not found", 404)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True


def create(port: int | None = None, token: str | None = None):
    port = config.UI_PORT if port is None else port
    token = token or secrets.token_urlsafe(24)
    server = Server(("127.0.0.1", port), None)
    server.RequestHandlerClass = make_handler(token, server.server_address[1])
    return server, token, f"http://localhost:{server.server_address[1]}/?token={token}"
