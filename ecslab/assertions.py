"""Named assertions used by scenario YAML.

Every function takes (aws, ctx, **params) and returns a Result. `aws` is the
dict of clients, `ctx` carries the scenario's cluster, its parsed seed and a
sleep function. Adding a new kind of check means adding one function here and
registering it; adding a scenario means YAML only.

For `success` rules, ok=True means the condition holds.
For `forbid` rules, ok=True means the forbidden shortcut WAS taken.
"""
from __future__ import annotations

import datetime
import json
import time
import urllib.parse
from dataclasses import dataclass, field


@dataclass
class Result:
    ok: bool
    reason: str


@dataclass
class Ctx:
    cluster: str
    seed: dict = field(default_factory=dict)
    sleep: callable = time.sleep
    settle_seconds: float = 8


REGISTRY = {}


def assertion(fn):
    REGISTRY[fn.__name__] = fn
    return fn


# ---- helpers --------------------------------------------------------------- #

def _service(aws, ctx, name):
    found = aws["ecs"].describe_services(cluster=ctx.cluster, services=[name]).get("services", [])
    return found[0] if found else None


def _target_group(aws, name):
    try:
        found = aws["elbv2"].describe_target_groups(Names=[name]).get("TargetGroups", [])
    except Exception:
        found = []
    return found[0] if found else None


def _revision(arn: str) -> int:
    return int(arn.rsplit(":", 1)[-1])


def _family(arn: str) -> str:
    return arn.rsplit("/", 1)[-1].rsplit(":", 1)[0]


def _image(aws, task_def: str) -> str | None:
    try:
        td = aws["ecs"].describe_task_definition(taskDefinition=task_def)["taskDefinition"]
        return td["containerDefinitions"][0]["image"]
    except Exception:
        return None


def _running(aws, ctx, service):
    arns = aws["ecs"].list_tasks(cluster=ctx.cluster, serviceName=service,
                                 desiredStatus="RUNNING").get("taskArns", [])
    if not arns:
        return []
    tasks = aws["ecs"].describe_tasks(cluster=ctx.cluster, tasks=arns).get("tasks", [])
    return [t for t in tasks if t.get("lastStatus") == "RUNNING"]


# ---- success --------------------------------------------------------------- #

@assertion
def service_stable(aws, ctx, service, min_uptime_seconds=0):
    """runningCount == desiredCount, on the service's current task definition, and
    still the same tasks a few seconds later.

    rolloutState is deliberately ignored: Floci 2.4.0 reports COMPLETED while a
    service is crash-looping at 0 running tasks (see PREFLIGHT_FINDINGS.md)."""
    def sample():
        svc = _service(aws, ctx, service)
        if not svc or svc.get("status") != "ACTIVE":
            return None, None, f"service {service} does not exist"
        tasks = _running(aws, ctx, service)
        current = [t["taskArn"] for t in tasks if t.get("taskDefinitionArn") == svc.get("taskDefinition")]
        return svc, sorted(current), None

    svc, first, missing = sample()
    if missing:
        return Result(False, missing)
    desired = svc.get("desiredCount", 0)
    if desired == 0 or len(first) != desired:
        return Result(False, f"{service}: {len(first)} of {desired} tasks running on the current task definition")
    ctx.sleep(ctx.settle_seconds)
    svc, second, missing = sample()
    if missing:
        return Result(False, missing)
    if second != first:
        return Result(False, f"{service}: tasks are still being replaced")
    if min_uptime_seconds:
        # For faults that let a task run for a while before it dies.
        now = datetime.datetime.now(datetime.timezone.utc)
        ages = []
        for t in _running(aws, ctx, service):
            started = t.get("startedAt")
            ages.append((now - started).total_seconds() if isinstance(started, datetime.datetime) else 0)
        if not ages or min(ages) < min_uptime_seconds:
            return Result(False, f"{service}: tasks have not stayed up for {min_uptime_seconds}s yet "
                                 f"(youngest is {int(min(ages or [0]))}s old)")
    return Result(True, f"{service}: {desired}/{desired} tasks running and staying up")


@assertion
def targets_healthy(aws, ctx, target_group):
    tg = _target_group(aws, target_group)
    if not tg:
        return Result(False, f"target group {target_group} does not exist")
    targets = aws["elbv2"].describe_target_health(
        TargetGroupArn=tg["TargetGroupArn"]).get("TargetHealthDescriptions", [])
    states = [t["TargetHealth"].get("State") for t in targets]
    if targets and all(s == "healthy" for s in states):
        return Result(True, f"{target_group}: {len(targets)} targets, all healthy")
    if not targets:
        return Result(False, f"{target_group}: no targets registered")
    # Floci can leave the targets of tasks that crashed after registering in the group, unhealthy,
    # long after those tasks are gone (AWS deregisters them). So one healthy target per wanted task
    # is enough, as long as nothing is still registering. service_stable separately proves the
    # wanted tasks are the ones running.
    wanted = (ctx.seed.get("service") or {}).get("desired_count")
    if wanted and states.count("healthy") >= wanted and "initial" not in states:
        return Result(True, f"{target_group}: {states.count('healthy')} healthy targets "
                            f"(ignoring {len(states) - states.count('healthy')} left behind by stopped tasks)")
    return Result(False, f"{target_group}: {states.count('healthy')} of {len(states)} targets healthy")


@assertion
def task_def_lineage(aws, ctx, service, derived_from, image=None):
    """The service runs revision `derived_from` or a later revision that still
    ships the same image. A later revision with the old image is a rollback.

    `image` names the image that must be running when revision `derived_from`
    itself has the wrong one (for example a mistyped tag)."""
    svc = _service(aws, ctx, service)
    if not svc:
        return Result(False, f"service {service} does not exist")
    current = svc["taskDefinition"]
    family = _family(current)
    want = image or _image(aws, f"{family}:{derived_from}")
    have = _image(aws, current)
    if _revision(current) >= derived_from and want and have == want:
        return Result(True, f"{service} runs {family}:{_revision(current)} with image {have}")
    return Result(False, f"{service} is not running revision {derived_from} or a fixed version of it")


@assertion
def task_def_lineage_is(aws, ctx, service, revision):
    """True when the service runs `revision` or any revision shipping its image."""
    svc = _service(aws, ctx, service)
    if not svc:
        return Result(False, f"service {service} does not exist")
    current = svc["taskDefinition"]
    family = _family(current)
    if _revision(current) == revision:
        return Result(True, f"{service} runs {family}:{revision}")
    want, have = _image(aws, f"{family}:{revision}"), _image(aws, current)
    return Result(bool(want) and want == have, f"{service} runs {family}:{_revision(current)}")


@assertion
def desired_count_at_least(aws, ctx, service, min):
    svc = _service(aws, ctx, service)
    n = (svc or {}).get("desiredCount", 0)
    return Result(n >= min, f"{service}: desiredCount is {n}, production needs {min}")


@assertion
def health_check_is(aws, ctx, target_group, path=None, matcher=None):
    """The target group's health check is configured as production requires."""
    tg = _target_group(aws, target_group)
    if not tg:
        return Result(False, f"target group {target_group} does not exist")
    if tg.get("HealthCheckEnabled") is False:
        return Result(False, f"{target_group}: health checks are disabled")
    if path and tg.get("HealthCheckPath") != path:
        return Result(False, f"{target_group}: the health check does not test the application's health endpoint")
    have = str((tg.get("Matcher") or {}).get("HttpCode"))
    if matcher and have != str(matcher):
        return Result(False, f"{target_group}: the health check does not expect the response a healthy app gives")
    return Result(True, f"{target_group}: health check is {tg.get('HealthCheckPath')} expecting {have}")


@assertion
def service_running(aws, ctx, service, min=1):
    """A supporting service has at least `min` tasks wanted and running."""
    svc = _service(aws, ctx, service)
    if not svc or svc.get("status") != "ACTIVE":
        return Result(False, f"service {service} does not exist")
    want, have = svc.get("desiredCount", 0), svc.get("runningCount", 0)
    return Result(want >= min and have >= min, f"{service}: {have} running, {want} desired; it needs at least {min}")


@assertion
def service_env(aws, ctx, service, name, value, message=None):
    """The service's current task definition sets this variable to this value."""
    svc = _service(aws, ctx, service)
    if not svc:
        return Result(False, f"service {service} does not exist")
    td = aws["ecs"].describe_task_definition(taskDefinition=svc["taskDefinition"])["taskDefinition"]
    env = {e["name"]: e["value"] for c in td["containerDefinitions"] for e in c.get("environment", [])}
    if env.get(name) == value:
        return Result(True, f"{service} still uses {name}")
    return Result(False, message or f"{service} no longer points {name} at the real dependency; "
                                    f"the dependency is required, not optional")


@assertion
def service_secret(aws, ctx, service, name, value_from_contains):
    """The service's current task definition passes this variable as a secret reference, not as a value."""
    svc = _service(aws, ctx, service)
    if not svc:
        return Result(False, f"service {service} does not exist")
    td = aws["ecs"].describe_task_definition(taskDefinition=svc["taskDefinition"])["taskDefinition"]
    containers = td["containerDefinitions"]
    if any(e["name"] == name for c in containers for e in c.get("environment", [])):
        return Result(False, f"{name} is a plain environment variable in {_family(svc['taskDefinition'])}:"
                             f"{_revision(svc['taskDefinition'])}; anyone who can read the task definition can read it")
    refs = [s.get("valueFrom", "") for c in containers for s in c.get("secrets", []) if s.get("name") == name]
    if any(value_from_contains in r for r in refs):
        return Result(True, f"{service} gets {name} from {refs[0]}")
    if refs:
        return Result(False, f"{name} is a secret, but it points at {refs[0]}")
    return Result(False, f"{service}'s task definition does not pass {name} to the container as a secret")


@assertion
def task_size(aws, ctx, service, min_memory_mib):
    svc = _service(aws, ctx, service)
    if not svc:
        return Result(False, f"service {service} does not exist")
    td = aws["ecs"].describe_task_definition(taskDefinition=svc["taskDefinition"])["taskDefinition"]
    have = int(td.get("memory") or 0)
    return Result(have >= min_memory_mib, f"{service} tasks have {have} MiB of memory; they need {min_memory_mib}")


def _security_group(aws, name):
    found = aws["ec2"].describe_security_groups(Filters=[{"Name": "group-name", "Values": [name]}]).get("SecurityGroups", [])
    return found[0] if found else None


def _rule_span(rule):
    if str(rule.get("IpProtocol")) == "-1":
        return 0, 65535
    return int(rule.get("FromPort", 0)), int(rule.get("ToPort", 65535))


@assertion
def security_group_allows(aws, ctx, group, port):
    sg = _security_group(aws, group)
    if not sg:
        return Result(False, f"security group {group} does not exist")
    ok = any(lo <= port <= hi for lo, hi in map(_rule_span, sg.get("IpPermissions", [])))
    return Result(ok, f"{group} {'allows' if ok else 'does not allow'} inbound traffic on port {port}")


@assertion
def service_uses_security_group(aws, ctx, service, group):
    svc, sg = _service(aws, ctx, service), _security_group(aws, group)
    if not svc or not sg:
        return Result(False, f"{service} or {group} does not exist")
    used = ((svc.get("networkConfiguration") or {}).get("awsvpcConfiguration") or {}).get("securityGroups", [])
    return Result(sg["GroupId"] in used, f"{service} is {'in' if sg['GroupId'] in used else 'not in'} security group {group}")


@assertion
def security_group_too_open(aws, ctx, group, max_ports=10):
    """Forbidden: an inbound rule that opens far more than the application needs."""
    sg = _security_group(aws, group)
    for rule in (sg or {}).get("IpPermissions", []):
        lo, hi = _rule_span(rule)
        if hi - lo + 1 > max_ports:
            return Result(True, f"{group} now allows inbound traffic on ports {lo}-{hi}. Opening everything gets the "
                                f"health check through and lets everything else in with it.")
    return Result(False, "")


@assertion
def role_policy_too_broad(aws, ctx, role):
    """Forbidden: fixing a missing permission by granting everything."""
    iam = aws["iam"]
    try:
        for attached in iam.list_attached_role_policies(RoleName=role).get("AttachedPolicies", []):
            if attached.get("PolicyName") in ("AdministratorAccess", "AmazonS3FullAccess", "PowerUserAccess"):
                return Result(True, f"{role} now has {attached['PolicyName']} attached. The app needed one permission on one bucket.")
        for name in iam.list_role_policies(RoleName=role).get("PolicyNames", []):
            doc = iam.get_role_policy(RoleName=role, PolicyName=name)["PolicyDocument"]
            doc = json.loads(urllib.parse.unquote(doc)) if isinstance(doc, str) else doc
            statements = doc.get("Statement", [])
            for st in statements if isinstance(statements, list) else [statements]:
                acts = st.get("Action", [])
                acts = [acts] if isinstance(acts, str) else acts
                res = st.get("Resource", [])
                res = [res] if isinstance(res, str) else res
                if st.get("Effect") == "Allow" and any(a == "*" or a.endswith(":*") for a in acts) and "*" in res:
                    return Result(True, f"policy {name} on {role} allows {acts} on every resource. Grant the one "
                                        f"action the app was denied, on the one resource it needs.")
    except Exception:
        pass
    return Result(False, "")


# ---- symptoms (used by `ecslab selftest` to prove the fault really reproduces) #

@assertion
def stopped_task_with(aws, ctx, service, stopped_reason_contains=None, exit_code=None, stop_code=None):
    """At least one stopped task of the service matches the given signature."""
    arns = aws["ecs"].list_tasks(cluster=ctx.cluster, serviceName=service, desiredStatus="STOPPED").get("taskArns", [])
    tasks = aws["ecs"].describe_tasks(cluster=ctx.cluster, tasks=arns[:100]).get("tasks", []) if arns else []
    seen = set()
    for t in tasks:
        codes = [c.get("exitCode") for c in t.get("containers", [])]
        seen.add((t.get("stopCode"), t.get("stoppedReason"), tuple(codes)))
        if stopped_reason_contains and stopped_reason_contains not in (t.get("stoppedReason") or ""):
            continue
        if stop_code and t.get("stopCode") != stop_code:
            continue
        if exit_code is not None and exit_code not in codes:
            continue
        return Result(True, f"stopped task: {t.get('stopCode')} / {t.get('stoppedReason')} / exit {codes}")
    return Result(False, f"no stopped task matches; saw {sorted(seen, key=str)[:3] or 'no stopped tasks'}")


@assertion
def log_contains(aws, ctx, log_group, text):
    try:
        events = aws["logs"].filter_log_events(logGroupName=log_group, limit=1000).get("events", [])
    except Exception as e:
        return Result(False, f"could not read {log_group}: {e}")
    hit = next((e for e in events if text in (e.get("message") or "")), None)
    if hit:
        return Result(True, f'{log_group} contains "{hit["message"].strip()}"')
    return Result(False, f'{log_group}: {len(events)} events, none contains "{text}"')


@assertion
def targets_unhealthy(aws, ctx, target_group):
    tg = _target_group(aws, target_group)
    if not tg:
        return Result(False, f"target group {target_group} does not exist")
    targets = aws["elbv2"].describe_target_health(TargetGroupArn=tg["TargetGroupArn"]).get("TargetHealthDescriptions", [])
    bad = [t for t in targets if t["TargetHealth"].get("State") == "unhealthy"]
    return Result(bool(bad), f"{target_group}: {len(bad)} of {len(targets)} targets unhealthy")


# ---- forbidden shortcuts --------------------------------------------------- #

@assertion
def desired_count_below(aws, ctx, service, min):
    svc = _service(aws, ctx, service)
    if not svc or svc.get("status") != "ACTIVE":
        return Result(True, f"{service} was deleted. A service that does not exist cannot fail, "
                            f"and cannot serve customers either.")
    n = svc.get("desiredCount", 0)
    if n < min:
        return Result(True, f"desiredCount on {service} is {n}; production needs {min}. Scaling a broken "
                            f"service down stops the failures by stopping the service.")
    return Result(False, "")


@assertion
def health_check_disabled(aws, ctx, target_group):
    tg = _target_group(aws, target_group)
    if not tg:
        return Result(True, f"target group {target_group} was deleted. Traffic has nowhere to go.")
    seed_hc = (ctx.seed.get("target_group") or {}).get("health_check", {})
    if tg.get("HealthCheckEnabled") is False:
        return Result(True, f"the health check on {target_group} is disabled. The load balancer will now "
                            f"send customers to tasks that cannot answer.")
    if tg.get("HealthCheckProtocol") == "TCP":
        return Result(True, f"the health check on {target_group} was reduced to a TCP connect. It no longer "
                            f"checks that the application answers.")
    want = seed_hc.get("path", "/health")
    if tg.get("HealthCheckPath") != want:
        return Result(True, f"the health check path on {target_group} was changed from {want} to "
                            f"{tg.get('HealthCheckPath')}. The check should prove the app is healthy, "
                            f"not be edited until it passes.")
    return Result(False, "")


@assertion
def health_check_matcher_widened(aws, ctx, target_group):
    tg = _target_group(aws, target_group)
    if not tg:
        return Result(False, "")
    want = str((ctx.seed.get("target_group") or {}).get("health_check", {}).get("matcher", "200"))
    have = str((tg.get("Matcher") or {}).get("HttpCode", want))
    if have != want:
        return Result(True, f"the health check matcher on {target_group} was widened from {want} to {have}. "
                            f"Accepting error responses as healthy hides the failure from the load balancer only.")
    return Result(False, "")


@assertion
def grace_period_above(aws, ctx, service, max_seconds):
    svc = _service(aws, ctx, service)
    grace = (svc or {}).get("healthCheckGracePeriodSeconds") or 0
    if grace > max_seconds:
        return Result(True, f"healthCheckGracePeriodSeconds on {service} is {grace}s (limit {max_seconds}s). "
                            f"A long grace period delays the failure; it does not remove it.")
    return Result(False, "")


def evaluate(rule: dict, aws, ctx) -> tuple[str, Result]:
    """A rule is a one-key mapping: {assertion_name: {params}}."""
    (name, params), = rule.items()
    if name == "not":                      # {not: {assertion: {...}}}
        inner, res = evaluate(params, aws, ctx)
        return f"not {inner}", Result(not res.ok, res.reason)
    if name not in REGISTRY:
        raise KeyError(f"unknown assertion '{name}'. Known: {', '.join(sorted(REGISTRY))}")
    return name, REGISTRY[name](aws, ctx, **(params or {}))
