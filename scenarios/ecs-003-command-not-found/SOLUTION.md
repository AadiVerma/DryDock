# ecs-003-command-not-found solution (spoilers)

**The start script nobody shipped**, difficulty 2/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

## What you should see while it is broken

- Stopped tasks with stop code `TaskFailedToStart`, stoppedReason containing "no such file or directory".

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

- The command in a task definition overrides the image's own CMD
- A command that does not exist in the image fails at container start, before any application log line is written
- stoppedReason quotes the runtime error, including the exact path it could not find
- Diff the failing revision against the last good one; the difference is usually the cause

## On real AWS

- On AWS the stoppedReason begins with CannotStartContainerError and quotes the same runtime message.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
- With the deployment circuit breaker enabled, AWS marks the deployment FAILED and rolls back by itself. The lab always reports the rollout as completed, so never trust that field here.
