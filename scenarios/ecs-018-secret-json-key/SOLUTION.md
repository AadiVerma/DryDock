# ecs-018-secret-json-key solution (spoilers)

**The right secret, the wrong key**, difficulty 3/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

The new version reads its database password from Secrets Manager.

## What you should see while it is broken

- Stopped tasks with stop code `TaskFailedToStart`, stoppedReason containing "ResourceInitializationError".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set `secrets` to `[{"name": "DB_PASSWORD", "valueFrom": "${secret:shop/prod/db}:password::"}]`.

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

- A Secrets Manager reference can select one JSON key: arn:...:secret:name:key::
- If that key is not in the secret, the task fails with ResourceInitializationError before the container starts
- The error quotes the full reference, including the key it could not find
- get-secret-value shows the keys that really exist

## On real AWS

- On AWS the execution role must also be allowed to read the secret or parameter; without it you get the same ResourceInitializationError with AccessDenied in the text. The lab does not check the execution role.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
