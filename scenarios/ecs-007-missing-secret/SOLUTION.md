# ecs-007-missing-secret solution (spoilers)

**The secret that moved**, difficulty 3/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

The new version reads its payments API key from Parameter Store.

## What you should see while it is broken

- Stopped tasks with stop code `TaskFailedToStart`, stoppedReason containing "ResourceInitializationError".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set `secrets` to `[{"name": "PAYMENTS_API_KEY", "valueFrom": "arn:aws:ssm:us-east-1:000000000000:parameter/shop/prod/payments/api-key"}]`.

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

- Secrets are fetched before the container starts; if one cannot be fetched the task fails with ResourceInitializationError
- stoppedReason includes the ARN of the secret that failed, which is usually enough to see the mistake
- The container never ran, so there are no application logs to read
- A secret reference is just a name; nothing checks it exists until a task tries to start

## On real AWS

- On AWS the execution role must also be allowed to read the secret or parameter; without it you get the same ResourceInitializationError with AccessDenied in the text. The lab does not check the execution role.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
- With the deployment circuit breaker enabled, AWS marks the deployment FAILED and rolls back by itself. The lab always reports the rollout as completed, so never trust that field here.
