# ecs-006-migration-fails solution (spoilers)

**Waiting on a migration**, difficulty 3/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

## What you should see while it is broken

- Stopped tasks with stop code `TaskFailedToStart`, stoppedReason containing "dependency container exited".
- A log line in `/ecs/checkout-api` containing "MIGRATIONS_DATABASE_URL is not set".

## The fix

1. Register a new revision based on checkout-api:8: in container `migrate`, set environment variable `MIGRATIONS_DATABASE_URL` to `postgres://checkout@db.shop.internal:5432/checkout`.

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

- dependsOn with condition SUCCESS means the app container only starts if the other container exits with code 0
- When a dependency fails, the app container never runs, so it has no logs; the dependency's logs hold the cause
- Each container in a task logs to its own stream: prefix/container-name/task-id
- Environment variables are set per container; the migration container does not inherit the app's

## On real AWS

- The service's Events tab on AWS names the failure (for example 'tasks failed to start'). In the lab, events only ever say the service reached a steady state.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
- With the deployment circuit breaker enabled, AWS marks the deployment FAILED and rolls back by itself. The lab always reports the rollout as completed, so never trust that field here.
