# ecs-027-user-then-filesystem solution (spoilers)

**Locked down twice**, difficulty 4/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

Revision 8 also includes the security team's container hardening.

## What you should see while it is broken

- Stopped tasks with stop code `TaskFailedToStart`, stoppedReason containing "unable to find user".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set `user` to `"app"`; in the app container, set `readonlyRootFilesystem` to `false`.

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

- Several changes in one revision can each break the deployment independently
- A startup failure in the runtime hides failures inside the application until it is fixed
- The first error you see is the first one in the startup sequence, not the only one
- Change one thing, redeploy, and read the newest stopped task again

## On real AWS

- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
- The service's Events tab on AWS names the failure (for example 'tasks failed to start'). In the lab, events only ever say the service reached a steady state.
