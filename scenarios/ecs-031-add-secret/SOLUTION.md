# ecs-031-add-secret solution (spoilers)

**Out of the environment**, difficulty 3/5.

## The task

Checkout needs the payments API key. It is already stored in Parameter Store
as /shop/prod/payments/api-key. Give it to the container as PAYMENTS_API_KEY
and deploy. The key must not appear in the task definition in plain text.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy.

## The fix

1. Register a new revision based on checkout-api:7: in the app container, set `secrets` to `[{"name": "PAYMENTS_API_KEY", "valueFrom": "arn:aws:ssm:us-east-1:000000000000:parameter/shop/prod/payments/api-key"}]`.

   ```bash
   aws ecs describe-task-definition --task-definition checkout-api:7 --query taskDefinition > td.json
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

- secrets in a container definition gives the container an environment variable whose value ECS fetches at start
- valueFrom is a reference (a Parameter Store or Secrets Manager ARN); the value never appears in the task definition
- Anyone who can read a task definition can read its environment, which is why keys do not belong there
- A wrong reference is only discovered when a task starts, so watch the first task of the deployment

## On real AWS

- On AWS the execution role must also be allowed to read the secret or parameter; without it you get the same ResourceInitializationError with AccessDenied in the text. The lab does not check the execution role.
- On AWS a deployment replaces tasks gradually, governed by minimumHealthyPercent and maximumPercent. The lab replaces them all at once.
