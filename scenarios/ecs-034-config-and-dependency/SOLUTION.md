# ecs-034-config-and-dependency solution (spoilers)

**Two lines short**, difficulty 4/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

Checkout calls the inventory service, which other teams run in the same cluster.

## What you should see while it is broken

- Stopped tasks with stoppedReason containing "Essential container", exit code 1.
- A log line in `/ecs/checkout-api` containing "missing required environment variable PAYMENTS_API_URL".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set environment variable `PAYMENTS_API_URL` to `https://payments.shop.internal`; in the app container, set environment variable `INVENTORY_URL` to `http://inventory.shop.internal:8080/health`.

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

- The same exit code can hide different errors; only the log tells them apart
- An application checks its configuration in order, so it reports one problem at a time
- Read the log of the newest stopped task after each fix, never an older one
- Compare the whole environment of the failing revision with the last good one, not just the line the error names

## On real AWS

- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
- Cloud Map DNS answers are cached for their TTL on AWS, so a recovered or renamed dependency can take up to a minute to resolve.
