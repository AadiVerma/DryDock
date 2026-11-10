# Writing a scenario

A scenario is two YAML files in `scenarios/<id>/`. No Python is needed unless
you want a new kind of check. The rule that matters most: **a scenario is not
finished until `ecslab selftest <id>` passes on the real emulator.** The unit
tests only prove the files are well formed.

## 1. Start from a failure Floci really produces

Floci does not behave like AWS everywhere. Before writing anything, look the
failure up in [PREFLIGHT_FINDINGS.md](../PREFLIGHT_FINDINGS.md). If it is not
there, add a probe to `preflight/classes.py` and run it:

```bash
ecslab start
python preflight/classes.py
```

Things that look like good scenarios and are not, because Floci does not
enforce them: essential sidecar exits, container health checks, circuit
breaker rollback, a missing execution role, a missing log group, task-level
memory, minimum/maximum healthy percent. ECS in Floci also ignores load
balancer health, so a scenario can never rely on tasks being replaced because
their target is unhealthy.

## 2. `seed.yaml`: the starting state

```yaml
cluster: shop-prod
emulator: {security_groups: true}      # optional: or {iam: true}. Never both.
images:                                # built locally, never pulled
- tag: ecslab/checkout-api:1.5.0
  context: images/checkout-api
  build_args: {APP_VERSION: 1.5.0}
task_definitions:
- family: checkout-api
  container: app
  container_port: 8080
  log_group: /ecs/checkout-api
  first_revision: 7                    # revisions below become 7, 8, ...
  revisions:
  - image: ecslab/checkout-api:1.4.2
    environment: {PORT: '8080'}
  - image: ecslab/checkout-api:1.5.0
    environment: {PORT: '9000'}        # the fault lives in a revision ...
    container: {memory: 128}           # ... any container definition field
    task_role: checkout-task-role      # ... or a task-level role
    sidecars: [{name: migrate, command: [...]}]
target_group: {name: checkout-tg, port: 8080, health_check: {...}}
load_balancer: {name: checkout-alb, listener_port: 8081}
service: {name: checkout-api, task_definition: checkout-api:7, desired_count: 2, ...}
# optional extras
ssm_parameters: {/shop/prod/key: value}
secrets: {shop/prod/db: {username: u, password: p}}
security_group: {name: checkout-sg, ingress: [{port: 8080, cidr: 0.0.0.0/0}]}
iam_roles: [{name: checkout-task-role, policies: {receipts: [...statements...]}}]
s3_buckets: [shop-receipts]
extra_services: [{name: inventory, image: ..., discovery: {namespace: shop.internal, name: inventory}}]
incident:                              # steps run after the healthy state is reached
- modify_target_group: {target_group: checkout-tg, path: /healthz}
```

The application image (`images/checkout-api/server.py`) has switches for most
faults: a required variable, a URL it must reach, a path it must write, memory
to allocate, a bucket it must list, an exit after N seconds. Read its header
before adding a new image.

## 3. `scenario.yaml`: the task, the checks, the lessons

| Key | What it is |
|---|---|
| `id`, `title`, `difficulty` (1 to 5), `estimated_minutes` | Shown in the scenario list |
| `topic` | The group it is listed under |
| `brief` | The task as it would reach someone at work. Never names the cause. |
| `success` | Rules that must all hold. Names are functions in `ecslab/assertions.py`. |
| `forbid` | Rules that mean a shortcut was taken. Checked first; any hit fails. |
| `partial` | Optional: a state worth acknowledging (a rollback) with a message |
| `hints` | Three, unlocking at 5, 10 and 15 minutes: where to look, what to notice, what to compare |
| `teaches` | Three or four sentences that are true of real AWS |
| `on_aws` | Where real AWS would have looked different from the lab |
| `selftest` | The proof (below) |
| `replay` | Rules for the replay: `first_move`, `key_call`, `answer_call`, `time_sinks`, `missed_key_call` |

There are three kinds:

- **Deploy goes wrong.** Starts healthy; `selftest.trap` deploys the broken revision.
- **Starts broken.** `incident:` in the seed breaks it before the learner arrives. No trap.
- **Task to carry out.** `selftest.task: true`. Starts healthy, nothing is broken; the
  success rules describe the change. A trap is optional (an attempt the API refuses).

### The `selftest:` block

```yaml
selftest:
  trap:                      # what the learner does that springs the fault
  - update_service: {service: checkout-api, task_definition: checkout-api:8}
  broken:                    # symptoms that must then be observable
  - stopped_task_with: {service: checkout-api, exit_code: 137}
  - log_contains: {log_group: /ecs/checkout-api, text: loading 300 MB}
  shortcuts:                 # each must be rejected
  - name: scale to zero
    expect: forbidden        # or partial, or failing
    steps: [...]
    undo: [...]
  solution:                  # must pass the check
  - register_revision: {base: checkout-api:8, set: {memory: 512}}
  - update_service: {service: checkout-api, task_definition: checkout-api:latest}
```

Steps available: `update_service`, `register_revision`, `modify_target_group`,
`set_security_group_ingress`, `put_role_policy`, `delete_role_policy`,
`expect_error`. They are in `ecslab/selftest.py`; a new one is one function.

The solution walkthrough (`SOLUTION.md`) is generated from these steps, so it
cannot drift from what is tested:

```bash
python -m ecslab.solution
```

## 4. Prove it

```bash
pytest tests/test_scenarios.py          # well formed, seeds, starts in the right state
ecslab start
ecslab selftest ecs-0NN-your-scenario   # the real proof
```

A failed self-test prints what the newest stopped tasks said and their last
log lines. The usual causes: Floci words an error differently from what
`broken:` expects; the fault is one Floci does not enforce; a shortcut's
`undo` does not put things back; targets left behind by crashed tasks.

Then play it yourself, without the solution open. If the hints did not help
or the brief gave the cause away, rewrite them.

## 5. Checklist

- [ ] The failure is in PREFLIGHT_FINDINGS.md as something Floci reproduces
- [ ] The brief does not name the cause
- [ ] Every way of hiding the symptom is in `forbid`, with a shortcut in the self-test
- [ ] Success rules never read `rolloutState` or service events
- [ ] `teaches` and `on_aws` are true of real AWS
- [ ] `ecslab selftest <id>` passes, twice in a row
- [ ] `python -m ecslab.solution` has been run and the README table updated
