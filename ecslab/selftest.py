"""End-to-end self-test for scenarios.

A scenario is only trustworthy if, on the real emulator:
  1. its seed reaches a healthy starting state,
  2. the trap really breaks things in the way the scenario says,
  3. every forbidden shortcut is rejected (and a rollback gets partial credit),
  4. the reference solution passes.

`ecslab selftest` proves all four from the scenario's own `selftest:` block, so
a scenario that Floci cannot reproduce fails here instead of in front of a
learner. Steps are AWS calls written declaratively; see ACTIONS below.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from . import config, runner
from .assertions import Ctx, evaluate


@dataclass
class Report:
    scenario: str
    rows: list = field(default_factory=list)      # (step, ok, detail)

    def add(self, step, ok, detail=""):
        self.rows.append((step, bool(ok), detail))
        return bool(ok)

    @property
    def ok(self):
        return bool(self.rows) and all(ok for _, ok, _ in self.rows)


# ---- step vocabulary ------------------------------------------------------- #

def _latest(aws, family):
    arns = [a for a in aws["ecs"].list_task_definitions(familyPrefix=family, sort="DESC").get("taskDefinitionArns", [])
            if f":task-definition/{family}:" in a]
    return max(arns, key=lambda a: int(a.rsplit(":", 1)[-1])).rsplit("/", 1)[-1]


def update_service(aws, sc, service, task_definition=None, desired_count=None, grace_period_seconds=None, force=False,
                   security_groups=None):
    kw = {}
    if security_groups:      # by name; subnets stay as they are
        from .seeder import security_group_id
        cur = aws["ecs"].describe_services(cluster=sc["cluster"], services=[service])["services"][0]
        vpc = dict((cur.get("networkConfiguration") or {}).get("awsvpcConfiguration") or {})
        vpc["securityGroups"] = [security_group_id(aws, n) for n in security_groups]
        kw["networkConfiguration"] = {"awsvpcConfiguration": vpc}
    if task_definition:
        family = task_definition.split(":")[0]
        kw["taskDefinition"] = _latest(aws, family) if task_definition.endswith(":latest") else task_definition
    if desired_count is not None:
        kw["desiredCount"] = desired_count
    if grace_period_seconds is not None:
        kw["healthCheckGracePeriodSeconds"] = grace_period_seconds
    if force:
        kw["forceNewDeployment"] = True
    aws["ecs"].update_service(cluster=sc["cluster"], service=service, **kw)
    return "UpdateService"


KEEP = ("family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions", "volumes",
        "placementConstraints", "requiresCompatibilities", "cpu", "memory", "runtimePlatform")


def register_revision(aws, sc, base, set_env=None, unset_env=None, image=None, container=None, set=None, top=None):
    """Register a new revision copied from `base`, with changes to one container."""
    if base.endswith(":latest"):
        base = _latest(aws, base.split(":")[0])
    td = aws["ecs"].describe_task_definition(taskDefinition=base)["taskDefinition"]
    new = {k: td[k] for k in KEEP if k in td}
    defs = new["containerDefinitions"]
    c = next((d for d in defs if d.get("name") == container), defs[0])
    env = {e["name"]: e["value"] for e in c.get("environment", [])}
    env.update({k: str(v) for k, v in (set_env or {}).items()})
    for k in unset_env or []:
        env.pop(k, None)
    c["environment"] = [{"name": k, "value": v} for k, v in env.items()]
    if image:
        c["image"] = image
    from .seeder import resolve
    c.update(resolve(aws, set or {}))    # any other container field, e.g. command, memory, secrets
    new.update(top or {})                # any task-level field, e.g. cpu, memory, taskRoleArn
    aws["ecs"].register_task_definition(**new)
    return "RegisterTaskDefinition"


def set_security_group_ingress(aws, sc, group, rules):
    from .seeder import security_group_id, set_ingress
    set_ingress(aws, security_group_id(aws, group), rules)
    return "AuthorizeSecurityGroupIngress"


def put_role_policy(aws, sc, role, name, statements):
    aws["iam"].put_role_policy(RoleName=role, PolicyName=name,
                               PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": statements}))
    return "PutRolePolicy"


def delete_role_policy(aws, sc, role, name):
    aws["iam"].delete_role_policy(RoleName=role, PolicyName=name)
    return "DeleteRolePolicy"


def expect_error(aws, sc, step, contains):
    """The step must be refused by the API, with this text in the error."""
    (name, params), = step.items()
    try:
        ACTIONS[name](aws, sc, **(params or {}))
    except Exception as e:
        if contains.lower() in str(e).lower():
            return name
        raise AssertionError(f"{name} failed, but not as expected: {e}")
    raise AssertionError(f"{name} was accepted; it should have been refused with '{contains}'")


def modify_target_group(aws, sc, target_group, **changes):
    arn = aws["elbv2"].describe_target_groups(Names=[target_group])["TargetGroups"][0]["TargetGroupArn"]
    names = {"path": "HealthCheckPath", "matcher": "Matcher", "enabled": "HealthCheckEnabled", "port": "HealthCheckPort",
             "protocol": "HealthCheckProtocol", "interval_seconds": "HealthCheckIntervalSeconds"}
    kw = {names[k]: ({"HttpCode": str(v)} if k == "matcher" else v) for k, v in changes.items()}
    aws["elbv2"].modify_target_group(TargetGroupArn=arn, **kw)
    return "ModifyTargetGroup"


ACTIONS = {"update_service": update_service, "register_revision": register_revision,
           "modify_target_group": modify_target_group, "set_security_group_ingress": set_security_group_ingress,
           "put_role_policy": put_role_policy, "delete_role_policy": delete_role_policy, "expect_error": expect_error}


def run_steps(aws, sc, steps, calls):
    for step in steps or []:
        (name, params), = step.items()
        if name not in ACTIONS:
            raise KeyError(f"unknown selftest step '{name}'. Known: {', '.join(sorted(ACTIONS))}")
        calls.append({"action": ACTIONS[name](aws, sc, **(params or {})), "ts": time.time()})


# ---- the test -------------------------------------------------------------- #

def _poll(fn, timeout, sleep, every=4):
    """Call fn() until it returns (True, detail) or time runs out. Returns the last result."""
    deadline = time.time() + timeout
    while True:
        ok, detail = fn()
        if ok or time.time() >= deadline:
            return ok, detail
        sleep(every)


def selftest(aws, scenario_id, sleep=time.sleep, timeout=120, settle_seconds=None, reset=None) -> Report:
    rep = Report(scenario_id)
    sc = runner.load(scenario_id)
    spec = sc.get("selftest")
    if not spec:
        rep.add("has a selftest block", False, "scenario.yaml has no `selftest:` section, so it cannot be verified")
        return rep
    settle = (0 if config.mock_enabled() else 8) if settle_seconds is None else settle_seconds
    ctx = Ctx(cluster=sc["cluster"], seed=sc["seed_spec"], sleep=sleep, settle_seconds=settle)
    calls = []
    check = lambda: runner.check(aws, sc, calls=calls, sleep=sleep, settle_seconds=settle)   # noqa: E731

    if reset:
        reset(sc)        # a clean emulator, with whatever settings this scenario needs
    try:
        runner.start(aws, sc, sleep=sleep)
    except Exception as e:
        rep.add("seed reaches a healthy starting state", False, str(e))
        return rep
    rep.add("seed reaches a healthy starting state", True)
    out = check()
    if sc["seed_spec"].get("incident"):
        rep.add("the scenario starts broken", out.status == runner.FAILING, f"{out.status}: {' '.join(out.messages)[:160]}")
    else:
        rep.add("check before doing anything says 'not started'", out.status == runner.NOT_STARTED, out.status)

    # the trap, and proof that it breaks things the way the scenario claims
    try:
        run_steps(aws, sc, spec.get("trap"), calls)
    except Exception as e:
        rep.add("trap can be sprung", False, str(e))
        return rep

    def broken():
        results = [evaluate(rule, aws, ctx) for rule in spec.get("broken", [])]
        bad = [f"{n}: {r.reason}" for n, r in results if not r.ok]
        return not bad, "; ".join(bad) or "; ".join(r.reason for _, r in results)
    if spec.get("broken"):
        ok, detail = _poll(broken, timeout, sleep)
        rep.add("the fault reproduces as described", ok, detail)
        out = check()
        rep.add("check while broken says 'failing'", out.status == runner.FAILING, f"{out.status}: {' '.join(out.messages)[:160]}")
    elif spec.get("trap"):   # a task scenario whose trap is an API call that gets refused
        rep.add("the obvious first attempt is refused", True)

    # shortcuts must not pass
    for sc_test in spec.get("shortcuts", []):
        want = sc_test.get("expect", runner.FORBIDDEN)
        try:
            run_steps(aws, sc, sc_test["steps"], calls)
            ok, detail = _poll(lambda: ((o := check()).status == want, f"{o.status}: {' '.join(o.messages)[:140]}"),
                               sc_test.get("timeout", 40), sleep)
            rep.add(f"shortcut '{sc_test['name']}' gives '{want}'", ok, detail)
            run_steps(aws, sc, sc_test.get("undo"), calls)
        except Exception as e:
            rep.add(f"shortcut '{sc_test['name']}' gives '{want}'", False, str(e))

    # the reference solution must pass
    try:
        run_steps(aws, sc, spec.get("solution"), calls)
    except Exception as e:
        rep.add("reference solution can be applied", False, str(e))
        return rep
    ok, detail = _poll(lambda: ((o := check()).status == runner.PASSED, f"{o.status}: {' '.join(o.messages)[:200]}"),
                       timeout, sleep)
    if not ok:
        detail += "\n         " + diagnose(aws, sc)
    rep.add("reference solution passes the check", ok, detail)
    return rep


def diagnose(aws, sc) -> str:
    """What the newest tasks looked like, so a failed self-test explains itself."""
    try:
        cluster, service = sc["cluster"], sc["seed_spec"]["service"]["name"]
        svc = aws["ecs"].describe_services(cluster=cluster, services=[service])["services"][0]
        td = svc.get("taskDefinition", "")
        arns = aws["ecs"].list_tasks(cluster=cluster, serviceName=service, desiredStatus="STOPPED").get("taskArns", [])
        tasks = aws["ecs"].describe_tasks(cluster=cluster, tasks=arns[:100]).get("tasks", []) if arns else []
        mine = [t for t in tasks if t.get("taskDefinitionArn") == td]
        seen = sorted({f"{t.get('stopCode')} | {(t.get('stoppedReason') or '').strip()[:220]} | exit "
                       f"{[c.get('exitCode') for c in t.get('containers', [])]}" for t in mine})
        group = sc["seed_spec"]["task_definitions"][0].get("log_group")
        lines = []
        try:
            ids = {t["taskArn"].rsplit("/", 1)[-1] for t in mine}
            events = aws["logs"].filter_log_events(logGroupName=group, limit=2000).get("events", [])
            for e in events:
                msg = (e.get("message") or "").strip()
                if e.get("logStreamName", "").rsplit("/", 1)[-1] in ids and msg and msg not in lines:
                    lines.append(msg)
        except Exception as e:
            lines = [f"<no logs: {e}>"]
        return (f"diagnosis: {len(mine)} stopped task(s) on {td.rsplit('/', 1)[-1]}: {seen[:3] or 'none'}; "
                f"their logs: {lines[-5:] or 'none'}")
    except Exception as e:
        return f"diagnosis unavailable: {e}"


def format_report(rep: Report) -> str:
    lines = [f"{rep.scenario}: {'VERIFIED' if rep.ok else 'FAILED'}"]
    for step, ok, detail in rep.rows:
        lines.append(f"  [{'ok' if ok else 'FAIL'}] {step}" + (f"\n         {detail}" if detail and not ok else ""))
    return "\n".join(lines)
