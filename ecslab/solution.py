"""Describe a scenario's reference fix in words and AWS CLI commands.

Built from the same `selftest:` steps that prove the scenario works, so the
walkthrough cannot drift away from what is actually tested.
"""
from __future__ import annotations

import json

from . import runner

C = "--cluster {cluster}"


def _step(sc, step) -> list[str]:
    (name, p), = step.items()
    cluster = sc["cluster"]
    if name == "update_service":
        bits, cmd = [], f"aws ecs update-service --cluster {cluster} --service {p['service']}"
        if p.get("task_definition"):
            td = p["task_definition"]
            td = td.replace(":latest", ":<the revision you just registered>")
            bits.append(f"deploy {td}"); cmd += f" --task-definition {td}"
        if p.get("desired_count") is not None:
            bits.append(f"set desired tasks to {p['desired_count']}"); cmd += f" --desired-count {p['desired_count']}"
        if p.get("grace_period_seconds") is not None:
            bits.append(f"set the health check grace period to {p['grace_period_seconds']}s")
            cmd += f" --health-check-grace-period-seconds {p['grace_period_seconds']}"
        if p.get("security_groups"):
            bits.append(f"put the tasks in security group {', '.join(p['security_groups'])}")
            cmd += " --network-configuration 'awsvpcConfiguration={subnets=[<same subnets>],securityGroups=[<group id>],assignPublicIp=ENABLED}'"
        if p.get("force"):
            bits.append("force a new deployment"); cmd += " --force-new-deployment"
        return [f"Update service {p['service']}: {'; '.join(bits)}.", cmd]
    if name == "register_revision":
        base = p["base"].replace(":latest", ":<the revision you just registered>")
        where = f"container `{p['container']}`" if p.get("container") else "the app container"
        changes = []
        for k, v in (p.get("set_env") or {}).items():
            changes.append(f"in {where}, set environment variable `{k}` to `{v}`")
        for k in p.get("unset_env") or []:
            changes.append(f"in {where}, remove environment variable `{k}`")
        if p.get("image"):
            changes.append(f"in {where}, set the image to `{p['image']}`")
        for k, v in (p.get("set") or {}).items():
            changes.append(f"in {where}, set `{k}` to `{json.dumps(v)}`")
        for k, v in (p.get("top") or {}).items():
            changes.append(f"at task level, set `{k}` to `{v}`")
        return [f"Register a new revision based on {base}: " + "; ".join(changes) + ".",
                f"aws ecs describe-task-definition --task-definition {base} --query taskDefinition > td.json",
                "# edit td.json as above, keep only the registerable fields, then:",
                "aws ecs register-task-definition --cli-input-json file://td.json"]
    if name == "modify_target_group":
        flags = {"path": "--health-check-path {}", "matcher": "--matcher HttpCode={}", "port": "--health-check-port {}"}
        changes = {k: v for k, v in p.items() if k != "target_group"}
        return [f"Change the health check on target group {p['target_group']}: "
                + ", ".join(f"{k} = {v}" for k, v in changes.items()) + ".",
                "aws elbv2 modify-target-group --target-group-arn <arn of " + p["target_group"] + "> "
                + " ".join(flags.get(k, "--" + k + " {}").format(v) for k, v in changes.items())]
    if name == "set_security_group_ingress":
        rules = ", ".join(f"TCP {r.get('port', str(r.get('from_port')) + '-' + str(r.get('to_port')))} from {r.get('cidr', '0.0.0.0/0')}"
                          for r in p["rules"]) or "none"
        out = [f"Make the inbound rules of security group {p['group']} exactly: {rules}.",
               f"aws ec2 revoke-security-group-ingress --group-id <id of {p['group']}> --ip-permissions <the wrong rule>"]
        for r in p["rules"]:
            if "port" in r:
                out.append(f"aws ec2 authorize-security-group-ingress --group-id <id of {p['group']}> --protocol tcp "
                           f"--port {r['port']} --cidr {r.get('cidr', '0.0.0.0/0')}")
        return out
    if name == "put_role_policy":
        doc = json.dumps({"Version": "2012-10-17", "Statement": p["statements"]})
        return [f"Give role {p['role']} an inline policy `{p['name']}` with exactly the permission that was denied.",
                f"aws iam put-role-policy --role-name {p['role']} --policy-name {p['name']} --policy-document '{doc}'"]
    if name == "delete_role_policy":
        return [f"Remove inline policy `{p['name']}` from role {p['role']}.",
                f"aws iam delete-role-policy --role-name {p['role']} --policy-name {p['name']}"]
    return [f"{name}: {json.dumps(p)}"]


def describe(scenario_id: str) -> str:
    sc = runner.load(scenario_id)
    st = sc.get("selftest", {})
    out = [f"# {sc['id']} solution (spoilers)", "", f"**{sc['title']}**, difficulty {sc['difficulty']}/5.", "",
           "## The task", "", sc["brief"].strip(), ""]
    incident = sc["seed_spec"].get("incident")
    if incident:
        out += ["## What was done to the environment", ""]
        for step in incident:
            out.append("- " + _step(sc, step)[0])
        out.append("")
    if st.get("broken"):
        out += ["## What you should see while it is broken", ""]
        for rule in st["broken"]:
            (name, params), = rule.items()
            if name == "stopped_task_with":
                bits = [f"stop code `{params['stop_code']}`" if params.get("stop_code") else "",
                        f"stoppedReason containing \"{params['stopped_reason_contains']}\"" if params.get("stopped_reason_contains") else "",
                        f"exit code {params['exit_code']}" if params.get("exit_code") is not None else ""]
                out.append("- Stopped tasks with " + ", ".join(b for b in bits if b) + ".")
            elif name == "log_contains":
                out.append(f"- A log line in `{params['log_group']}` containing \"{params['text']}\".")
            elif name == "targets_unhealthy":
                out.append(f"- Unhealthy targets in `{params['target_group']}`.")
        out.append("")
    out += ["## The fix", ""]
    for i, step in enumerate(st.get("solution", []), 1):
        lines = _step(sc, step)
        out += [f"{i}. {lines[0]}", "", "   ```bash"] + ["   " + ln for ln in lines[1:]] + ["   ```", ""]
    shortcuts = [s for s in st.get("shortcuts", []) if s.get("expect", "forbidden") == "forbidden"]
    if shortcuts:
        out += ["## What does not count as a fix", ""] + [f"- {s['name'].capitalize()}." for s in shortcuts] + [""]
    out += ["## What it teaches", ""] + [f"- {t}" for t in sc.get("teaches", [])] + [""]
    if sc.get("on_aws"):
        out += ["## On real AWS", ""] + [f"- {t}" for t in sc["on_aws"]] + [""]
    return "\n".join(out)


def write_all() -> list[str]:
    """Write SOLUTION.md beside every scenario. A hand-written one (it does not start with the generated title) is left alone."""
    from . import config
    written = []
    for sid in runner.available():
        path = config.SCENARIOS_DIR / sid / "SOLUTION.md"
        if path.exists() and not path.read_text().startswith(f"# {sid} solution (spoilers)"):
            continue
        path.write_text(describe(sid).rstrip("\n") + "\n")
        written.append(sid)
    return written


if __name__ == "__main__":
    print(f"wrote {len(write_all())} walkthroughs")
