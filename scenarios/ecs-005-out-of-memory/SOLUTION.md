# ecs-005-out-of-memory solution (spoilers)

**Killed without a word**, difficulty 3/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

## What you should see while it is broken

- Stopped tasks with stoppedReason containing "Essential container", exit code 137.
- A log line in `/ecs/checkout-api` containing "loading 300 MB into memory".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set `memory` to `512`.

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

- Exit code 137 means the container was killed with SIGKILL; with no error in the logs, suspect the memory limit
- The logs of a killed container simply stop: the last line shows what it was doing when it died
- A container-level memory value is a hard limit; exceed it and the container is killed
- Size the limit from what the application needs at startup, not from what the old version needed

## On real AWS

- On AWS the container's reason reads 'OutOfMemoryError: Container killed due to memory usage', so you are told directly. The lab gives you only exit code 137.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
