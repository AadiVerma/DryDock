# ecs-004-wrong-entrypoint-path solution (spoilers)

**It starts, then it doesn't**, difficulty 2/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

## What you should see while it is broken

- Stopped tasks with stoppedReason containing "Essential container", exit code 2.
- A log line in `/ecs/checkout-api` containing "can't open file '/app/main.py'".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set `command` to `["python", "/app/server.py"]`.

   ```bash
   aws ecs describe-task-definition --task-definition checkout-api:8 --query taskDefinition > td.json
   # edit td.json as above, keep only the registerable fields, then:
   aws ecs register-task-definition --cli-input-json file://td.json
   ```

2. Update service checkout-api: deploy checkout-api:<the revision you just registered>.

   ```bash
   aws ecs update-service --cluster shop-prod --service checkout-api --task-definition checkout-api:<the revision you just registered>
   ```

## What does not count as a fix

- Scale to zero.

## What it teaches

- 'Essential container in task exited' means the container started and then quit; the exit code says how
- Exit code 2 from an interpreter usually means it could not open the script it was told to run
- When the container starts, its own logs explain the exit; when it never starts, stoppedReason does
- The log stream for a task is named prefix/container/task-id

## On real AWS

- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
- The service's Events tab on AWS names the failure (for example 'tasks failed to start'). In the lab, events only ever say the service reached a steady state.
- With the deployment circuit breaker enabled, AWS marks the deployment FAILED and rolls back by itself. The lab always reports the rollout as completed, so never trust that field here.
