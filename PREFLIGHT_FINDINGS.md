# Preflight findings

Run on 10 Nov 2026 against `floci/floci:latest` = **2.4.0** (commit `9be236e`,
image `sha256:5ee85db6…`), Docker 28.1.1, Apple Silicon. Raw evidence:
`preflight/results/preflight-20261110-133146.json`.

## Verdict: the ALB port-mismatch scenario is not viable

The ALB sees the port mismatch. ECS does nothing about it.

| Question | Measured | Result |
|---|---|---|
| Cluster, task definition, target group, ALB, listener, service with load balancer | All created | PASS |
| Tasks run as real containers | 2/2 running in 15s | PASS |
| ALB health check passes on a correct container | Both targets `healthy` in 15s | PASS |
| ALB health check fails on the mismatched container | Both targets `unhealthy`, reason `Target.FailedHealthChecks` | PASS |
| Failed health check feeds back into ECS | No. 2 bad tasks launched, 0 stopped, service sits at 2/2 | **FAIL** |
| `rolloutState` reflects the failure | No. `COMPLETED` after 6s with unhealthy targets | **FAIL** |
| Service events describe the failure | Only ever "has reached a steady state" | **FAIL** for teaching |
| `stoppedReason` populated | Yes, but only for the old tasks: "Service deployment replaced task definition …" | PARTIAL |
| Container logs through CloudWatch Logs | Yes: "checkout-api listening on port 3000" | PASS |
| Fallback 1: failing container `healthCheck` | Task stays RUNNING, `healthStatus` stays `UNKNOWN` for 75s | **FAIL** |
| Fallback 2: container exits on startup | 14 tasks launched and stopped in 70s, `stoppedReason` "Essential container in task exited", `stopCode` `EssentialContainerExited`, exit code 1 | Works, with caveats below |

Timing is not a problem: a deployment cycle is about 15 seconds.

**Decision (10 Nov): scenario 1 is now crash-on-startup**, see
`scenarios/ecs-001-crash-on-deploy/`.

## Three things the runner must not rely on

1. **`rolloutState` is not a health signal.** It read `COMPLETED` while
   `runningCount` was 0/2 during the baseline, and stayed `COMPLETED` for the whole
   crash loop at 0/1. The `service_stable` assertion uses
   `runningCount == desiredCount` with the same tasks still running a few seconds
   later, never `rolloutState`.
2. **Service events are not a diagnostic.** The crash-looping service reported
   "has reached a steady state" throughout. The PRD's first lesson, "read service
   events before anything else", cannot be taught on Floci 2.4.0.
3. **There is no backoff.** A crashing task is replaced roughly every 5 seconds.
   A 20-minute lab piles up around 240 stopped tasks per desired task.

## Differences from what the source reading predicted

- Predicted fallback 1 would show `healthStatus: UNHEALTHY` without replacing the
  task. Measured `UNKNOWN`: the container health check had no visible effect at
  all. Not investigated further, since it fails either way.
- Predicted `rolloutState` would wait for `runningCount` to reach `desiredCount`.
  Measured: it reports `COMPLETED` before any task is running.

Everything else matched.

## Not yet measured

- During a deployment where the new revision crashes, do the old revision's tasks
  keep serving, as on AWS? **Probably yes.** First real lab run (10 Nov): 20s
  after deploying revision 8, `checkout-tg` had 4 targets, 2 healthy, which fits
  revision 7's two tasks still serving beside two crashing ones. Inferred from
  the target count, not yet confirmed by listing tasks.
- Whether the deployment circuit breaker changes any of this. It is off in the
  seed.

## Configuration confirmed working

- `FLOCI_SERVICES_ECS_DOCKER_NETWORK=ecslab`: the ALB reaches task containers at
  their network address (172.18.0.x:8080).
- `FLOCI_SERVICES_ECS_IMAGE_PULL_BEHAVIOR=prefer-cached`: the locally built image
  runs with no registry.
- Fargate + `awsvpc` + `ip` target group.
- The compose file now pins `floci/floci:2.4.0`.

## Failure modes (probe of 10 Nov, `preflight/classes.py`)

`ecs-001` passed `ecslab selftest` end to end on Floci 2.4.0. The probe then
launched 23 candidate failure modes. Evidence:
`preflight/results/classes-20261110-150629.json`.

| Failure mode | What Floci did | Usable |
|---|---|---|
| Required config missing | Loops, exit 1, FATAL line in logs | Yes (`ecs-001`) |
| Image tag does not exist | Loops, `TaskFailedToStart`, "pull access denied ... repository does not exist" | Yes (`ecs-002`) |
| Command not in image | Loops, `TaskFailedToStart`, runtime error naming the path | Yes (`ecs-003`) |
| Wrong script path | Loops, exit 2, "can't open file" in logs | Yes (`ecs-004`) |
| Over memory limit | Loops, exit 137. stoppedReason is generic; no OutOfMemoryError text | Yes (`ecs-005`) |
| dependsOn SUCCESS, dependency fails | Loops, `TaskFailedToStart`, "A dependency container exited with code 1" | Yes (`ecs-006`) |
| Secret does not exist | Loops, `ResourceInitializationError` naming the parameter | Yes (`ecs-007`) |
| Secret exists | Injected as an environment variable | Supports the fix |
| Read-only root filesystem | Loops, exit 1, "Read-only file system" in logs | Yes (`ecs-008`) |
| User not in image | Loops, `TaskFailedToStart`, "unable to find user" | Yes (`ecs-009`) |
| App listens on the wrong port | Runs; targets unhealthy `Target.FailedHealthChecks` | Yes (`ecs-010`) |
| Health endpoint returns 503 | Runs; targets unhealthy `Target.ResponseCodeMismatch` | Yes (`ecs-011`, `ecs-012`) |
| Invalid Fargate CPU/memory pair | Rejected: "No Fargate configuration exists for given values" | Not used yet |
| Crash 15s after start | Flaps, but the service reports 1/1 and COMPLETED | Not used yet |
| App ignores SIGTERM | Stop works; exit 137, stop code `UserInitiated` | Not used yet |
| Dependency unreachable at startup | Loops, exit 1, "Name does not resolve" | Not used: needs a second service |
| Slow start (25s) | Target healthy once the app listens | Not a failure |
| Essential sidecar exits | **Task keeps running.** AWS would stop the task | No |
| Non-essential sidecar exits | Task keeps running, as on AWS | n/a |
| Failing container healthCheck | **Ignored.** healthStatus stays UNKNOWN | No |
| Deployment circuit breaker with rollback | **No rollback** within 65s | No |
| Wrong health check path | Inconclusive: the probe image answered 200 on every path. Fixed since | `ecs-011` tests it |

Not probed: IAM permission failures, security groups, service discovery.

## Second probe (10 Nov, `preflight/classes2.py`)

Evidence: `preflight/results/classes2-20261110-172537.json`.

| Area | What Floci did | Usable |
|---|---|---|
| Service discovery (Cloud Map) | A second service reached `inventory.shop.internal` by name | Yes (`ecs-015` to `ecs-017`) |
| Secrets Manager, JSON key | Injected; a key that is not in the secret gives `ResourceInitializationError` | Yes (`ecs-018`) |
| Secrets Manager, missing secret | `ResourceInitializationError` | Covered by `ecs-007` |
| Security groups, enforcement on | Group allowing 8080: healthy. No rule, or only 443: `Target.Timeout` | Yes (`ecs-019`, `ecs-020`) |
| Default security group, enforcement on | `Target.Timeout`: it does not admit the load balancer | Scenarios create their own group |
| IAM enforcement + task role | Role with the permission: app starts. Without it: `AccessDenied`, exit 1 | Yes (`ecs-021` to `ecs-023`) |
| The lab's own credentials under enforcement | Treated as the account root; all calls still work | n/a |
| No task role, or a role that does not exist | Floci hands the container static keys, so the app still worked | The app image now ignores static keys; `ecs-022` tests this |
| Missing environment file in S3 | Ignored; task runs | No |
| Secrets with no execution role | Not enforced | No |
| Log group missing, no create flag | Task runs; its logs are lost | No (AWS fails the task) |
| Task-level memory exceeded | Not enforced | No |
| Minimum healthy 100% and maximum 100% | Deployment proceeds anyway | No |
| Service names a container not in the task definition | Accepted; no targets registered | No (AWS rejects it) |
| Target type `instance` with awsvpc | Accepted and healthy | No |
| API validation | Rejects bridge mode on Fargate, a missing revision, missing network configuration. Accepts no essential container, duplicate names, an inactive revision, a nonexistent subnet | Not used |

IAM enforcement and security group enforcement are separate Floci settings and
are never on together: Floci's docs say task role credentials do not work under
security group enforcement.
