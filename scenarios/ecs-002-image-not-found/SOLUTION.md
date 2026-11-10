# ecs-002-image-not-found solution (spoilers)

**The image that isn't there**, difficulty 1/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

## What you should see while it is broken

- Stopped tasks with stop code `TaskFailedToStart`, stoppedReason containing "pull access denied".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set the image to `ecslab/checkout-api:1.5.0`.

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

- A task that cannot start stops with TaskFailedToStart, and stoppedReason carries the container runtime's error
- 'pull access denied' or 'not found' means the image name or tag is wrong, or the registry is unreachable
- A task that fails before its container starts writes no application logs, so the logs are the wrong place to look
- A task definition is immutable: fixing it means registering a new revision

## On real AWS

- On AWS the stoppedReason begins with CannotPullContainerError and quotes the registry's answer.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
- With the deployment circuit breaker enabled, AWS marks the deployment FAILED and rolls back by itself. The lab always reports the rollout as completed, so never trust that field here.
