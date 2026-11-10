# ecs-009-unknown-user solution (spoilers)

**Running as nobody in particular**, difficulty 2/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

Revision 8 also stops the container running as root.

## What you should see while it is broken

- Stopped tasks with stop code `TaskFailedToStart`, stoppedReason containing "unable to find user".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set `user` to `"app"`.

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

- The user field tells the container which user to run as; the user must exist in the image
- A user that does not exist fails at container start with a runtime error, before the application runs
- Running as a non-root user is good practice, but the user has to be created when the image is built
- No application logs exist for a container that never started

## On real AWS

- On AWS the stoppedReason begins with CannotStartContainerError and quotes the same runtime message.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
