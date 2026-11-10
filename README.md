# ECS Lab

**Practise fixing broken Amazon ECS deployments on your own laptop, with real containers, no AWS account and no bill.**

ECS Lab is a local training simulator. It gives you a realistic ECS environment
(a cluster, a service behind a load balancer, task definitions, logs, IAM roles,
security groups) and then hands you a deployment that is broken in a way that
happens in production all the time. You investigate with the ordinary AWS CLI or
with console-style web pages, fix it, and the lab tells you whether you actually
fixed it and how you went about it.

<br />

## Why

Reading about ECS does not teach you what to do when a deployment will not
settle at two in the morning. Practising on a real AWS account costs money,
needs permissions most learners do not have, and nobody wants you to break
production to learn. ECS Lab is a safe place to build the habit that matters:
read the stopped task, read the logs, compare with the last good revision, fix
the cause rather than the symptom.

## What is in it

ECS Lab is not an emulator. [Floci](https://github.com/floci-io/floci) emulates
the AWS APIs and runs the containers; ECS Lab supplies the scenarios, the
checking and the replay.

## Quickstart

You need:

```bash
git clone <your-repository-url> ecs-lab
cd ecs-lab
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

ecslab start    # builds the task images, starts the emulator and the lab proxy
ecslab ui       # opens the web interface in your browser
```

Pick a scenario and press **Start**. If something will not start, run
`ecslab doctor`: it checks Docker, the AWS CLI, ports and disk space and says
what to fix.

So far the lab has only been run on macOS on Apple Silicon. Linux should work;
Windows is untested.

## Using it

### In the browser

The first page lists the scenarios by topic with their difficulty, what you have
solved and your best time. Once a scenario is running you can work in two ways,
and mix them freely:

<br />

**Hint**, **Check my fix** and **Reset** are in the top bar. **Trail** shows
every AWS call you have made, typed or clicked.

When the check passes you get:

### In a terminal only

```bash
ecslab run ecs-001-crash-on-deploy    # sets up the environment and prints the task
eval "$(ecslab env)"                  # points the AWS CLI in this terminal at the lab

aws ecs describe-services --cluster shop-prod --services checkout-api
# ... investigate and fix ...

ecslab check    # did you really fix it?
ecslab hint     # hints unlock at 5, 10 and 15 minutes
ecslab reset    # throw the environment away and start clean
```

### What a scenario looks like

```
The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.
```

You deploy it, it goes wrong, and the brief never tells you why. A check that
fails says what is still wrong. A shortcut gets this:

```
That makes the symptom go away without fixing anything:
  - desiredCount on checkout-api is 0; production needs 2. Scaling a broken service down stops the failures by stopping the service.
Undo it and fix the cause.
```

## Scenarios

There are three kinds. **Deploy goes wrong**: everything is healthy until you
deploy the revision you were asked to. **Starts broken**: something changed
before you arrived and nothing was deployed. **Task to carry out**: nothing is
broken; make a change without breaking it.

**Tasks that start and then die**

| Scenario                        | Title                              | Difficulty |
| ------------------------------- | ---------------------------------- | ---------- |
| `ecs-001-crash-on-deploy`       | The deployment that never finishes | 2/5        |
| `ecs-004-wrong-entrypoint-path` | It starts, then it doesn't         | 2/5        |
| `ecs-005-out-of-memory`         | Killed without a word              | 3/5        |
| `ecs-008-read-only-filesystem`  | Hardened into a corner             | 3/5        |
| `ecs-025-crash-after-start`     | Fine for fifteen seconds           | 4/5        |

**Tasks that never start**

| Scenario                    | Title                           | Difficulty |
| --------------------------- | ------------------------------- | ---------- |
| `ecs-002-image-not-found`   | The image that isn't there      | 1/5        |
| `ecs-003-command-not-found` | The start script nobody shipped | 2/5        |
| `ecs-009-unknown-user`      | Running as nobody in particular | 2/5        |
| `ecs-006-migration-fails`   | Waiting on a migration          | 3/5        |
| `ecs-007-missing-secret`    | The secret that moved           | 3/5        |
| `ecs-018-secret-json-key`   | The right secret, the wrong key | 3/5        |

**Running but not serving**

| Scenario                    | Title                   | Difficulty |
| --------------------------- | ----------------------- | ---------- |
| `ecs-013-scaled-to-zero`    | Where did everybody go  | 1/5        |
| `ecs-011-health-check-path` | Nothing was deployed    | 2/5        |
| `ecs-012-health-check-code` | Healthy, apparently not | 2/5        |
| `ecs-010-wrong-port`        | Green deploy, dead site | 3/5        |

**Dependencies**

| Scenario                  | Title                    | Difficulty |
| ------------------------- | ------------------------ | ---------- |
| `ecs-015-dependency-name` | A name that goes nowhere | 2/5        |
| `ecs-017-dependency-port` | Right name, wrong door   | 2/5        |
| `ecs-016-dependency-down` | Somebody else's outage   | 3/5        |

**Security groups**

| Scenario                       | Title              | Difficulty |
| ------------------------------ | ------------------ | ---------- |
| `ecs-019-security-group-rule`  | A tidy-up too far  | 3/5        |
| `ecs-020-wrong-security-group` | In the wrong group | 3/5        |

**IAM**

| Scenario                       | Title                   | Difficulty |
| ------------------------------ | ----------------------- | ---------- |
| `ecs-021-task-role-permission` | Access denied           | 3/5        |
| `ecs-022-no-task-role`         | Who am I                | 3/5        |
| `ecs-023-policy-wrong-bucket`  | Almost the right bucket | 4/5        |

**More than one thing wrong**

| Scenario                             | Title                    | Difficulty |
| ------------------------------------ | ------------------------ | ---------- |
| `ecs-032-zero-and-health-check`      | Back up, still down      | 3/5        |
| `ecs-014-two-faults`                 | One fix is not enough    | 4/5        |
| `ecs-026-secret-then-memory`         | Two for the price of one | 4/5        |
| `ecs-027-user-then-filesystem`       | Locked down twice        | 4/5        |
| `ecs-033-security-group-and-matcher` | Reviewed twice           | 4/5        |
| `ecs-034-config-and-dependency`      | Two lines short          | 4/5        |
| `ecs-028-three-faults`               | The long afternoon       | 5/5        |
| `ecs-035-no-role-wrong-policy`       | No one, then not allowed | 5/5        |

**Changes to make**

| Scenario                           | Title                  | Difficulty |
| ---------------------------------- | ---------------------- | ---------- |
| `ecs-029-scale-out`                | Four for the sale      | 1/5        |
| `ecs-024-resize-task`              | More memory, please    | 2/5        |
| `ecs-030-add-environment-variable` | Switch it on           | 2/5        |
| `ecs-031-add-secret`               | Out of the environment | 3/5        |

A few scenarios need an emulator feature that is off by default (security group
or IAM enforcement). The lab restarts the emulator with the right setting when
you start one.

## Commands

| Command                      | What it does                                                                                                                 |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `ecslab doctor`              | Checks Docker, the AWS CLI, ports, disk space and the emulator image, and says how to fix what is wrong.                     |
| `ecslab start`               | Builds the task images locally, starts Floci (port 4566) and the logging proxy (port 4567).                                  |
| `ecslab ui`                  | Serves the web interface on localhost:4568 and opens it. Ctrl-C stops it.                                                    |
| `ecslab run <scenario>`      | Builds the scenario's starting state, waits until it is healthy, prints the task.                                            |
| `ecslab env`                 | Prints the `export` lines that point the AWS CLI at the lab.                                                                 |
| `ecslab check`               | Applies the forbidden-shortcut rules, then the success rules. Prints the replay on a pass.                                   |
| `ecslab hint`                | Shows the next hint once enough time has passed.                                                                             |
| `ecslab solution <scenario>` | Prints the reference fix. Spoilers.                                                                                          |
| `ecslab reset`               | Removes the task containers and restarts the emulator. `--stop` shuts everything down. `--forget` also clears your progress. |
| `ecslab selftest`            | Proves scenarios end to end on the emulator.                                                                                 |

## How it works

```
aws cli / web pages ──> lab proxy :4567 ──> Floci :4566 ──> Docker (your tasks)
                             │
                             └─ .ecslab/calls.jsonl ──> trail and replay
```

The built-in terminal is a real shell on your machine. The server therefore
listens on 127.0.0.1 only, needs the private link `ecslab ui` prints, and
refuses requests from other websites. Do not expose the port on a network.

## Verified scenarios

A scenario only ships if it is proven on the real emulator. Each one carries a
`selftest:` block with the trap, the symptoms the fault must show, the shortcuts
that must be rejected and a reference solution.

```bash
ecslab start
ecslab selftest                         # every scenario, about 90 minutes
ecslab selftest --from ecs-029          # that one and everything after it
ecslab selftest ecs-005-out-of-memory   # just one
```

For each scenario it builds the starting state, springs the trap, confirms the
fault appears as described, confirms every shortcut is rejected, applies the
solution and confirms the check passes.

All 35 have passed on Floci 2.4.0 (10 Nov 2026): `ecs-001` to `ecs-028` in one
run and `ecs-029` to `ecs-035` in a second.

## Where the lab differs from real AWS

The emulator is not AWS. These differences were measured against Floci 2.4.0;
details are in [PREFLIGHT\_FINDINGS.md](PREFLIGHT_FINDINGS.md).

The lab's checks do not depend on any of these, and each scenario's "On real
AWS" notes say what you would have seen there instead.

## Development

```bash
pip install -e '.[dev]'
pytest                      # 147 tests, no Docker needed
ECSLAB_MOCK=1 ecslab ui     # the whole interface against an in-memory fake
```

Mock mode (`ecslab/mock.py`) fakes the AWS calls the lab makes, so the seeder,
checks, runner and web interface can be tested without Docker. It models the
fault in `ecs-001` and the scenarios that only change configuration; other
faults do not appear in mock mode. It is a test double, not an emulator.

### Layout

```
ecslab/                       cli, runner, seeder, assertions, proxy, replay, progress, solution, mock
ecslab/ui/                    web interface: server.py and static/ (xterm.js vendored)
scenarios/<id>/               scenario.yaml, seed.yaml, SOLUTION.md (spoilers, generated)
images/checkout-api/          the task image, built locally in two versions
preflight/                    probes that measure what the emulator really does
docs/                         scenario authoring guide, screenshots
tests/                        unit tests in mock mode
docker-compose.yml            Floci, pinned to 2.4.0
```

## Status

This is an early version, built and tested by one person on one machine.

## Acknowledgements and disclaimer

ECS Lab depends on [Floci](https://github.com/floci-io/floci) for AWS emulation
and vendors [xterm.js](https://xtermjs.org/) for the terminal.

ECS Lab is an independent training project. It is not affiliated with, endorsed
by or sponsored by Amazon Web Services. "AWS", "Amazon ECS" and related names
are trademarks of Amazon.com, Inc. or its affiliates. The pages follow the
structure and wording of the AWS consoles so that what you learn transfers; the
visual design is ECS Lab's own.

## Licence

No licence has been chosen yet. Until one is added, the code is not licensed
for reuse.
