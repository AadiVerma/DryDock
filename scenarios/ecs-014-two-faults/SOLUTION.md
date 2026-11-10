# ecs-014-two-faults solution (spoilers)

**One fix is not enough**, difficulty 4/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

The new version reads its payments API key from Parameter Store.

## What you should see while it is broken

- Stopped tasks with stop code `TaskFailedToStart`.

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set the image to `ecslab/checkout-api:1.5.0`; in the app container, set `secrets` to `[{"name": "PAYMENTS_API_KEY", "valueFrom": "arn:aws:ssm:us-east-1:000000000000:parameter/shop/prod/payments/api-key"}]`.

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

- A deployment can have more than one thing wrong; fixing the first only reveals the second
- Always re-read the newest stopped task after a fix: the reason may have changed
- Secrets are fetched before the image is pulled, so a bad secret reference can hide a bad image tag, or the reverse
- Verify a fix by its effect on new tasks, not by the fact that you made a change

## On real AWS

- On AWS the execution role must also be allowed to read the secret or parameter; without it you get the same ResourceInitializationError with AccessDenied in the text. The lab does not check the execution role.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
- With the deployment circuit breaker enabled, AWS marks the deployment FAILED and rolls back by itself. The lab always reports the rollout as completed, so never trust that field here.
