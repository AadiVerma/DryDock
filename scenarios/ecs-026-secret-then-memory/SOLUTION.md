# ecs-026-secret-then-memory solution (spoilers)

**Two for the price of one**, difficulty 4/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

The new version reads its database password from Secrets Manager.

## What you should see while it is broken

- Stopped tasks with stop code `TaskFailedToStart`, stoppedReason containing "ResourceInitializationError".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set `memory` to `512`; in the app container, set `secrets` to `[{"name": "DB_PASSWORD", "valueFrom": "${secret:shop/prod/db}:password::"}]`.

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

- Fixing one fault can expose another that was hidden behind it
- A task that fails before its container starts cannot reveal problems inside the container
- Re-read the newest stopped task after every change: a different stop code is progress
- Exit code 137 with no error in the logs points at the memory limit

## On real AWS

- On AWS the execution role must also be allowed to read the secret or parameter; without it you get the same ResourceInitializationError with AccessDenied in the text. The lab does not check the execution role.
- On AWS the second failure names itself: 'OutOfMemoryError: Container killed due to memory usage'.
