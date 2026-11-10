# ecs-017-dependency-port solution (spoilers)

**Right name, wrong door**, difficulty 2/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

Checkout calls the inventory service, which other teams run in the same cluster.

## What you should see while it is broken

- Stopped tasks with stoppedReason containing "Essential container", exit code 1.
- A log line in `/ecs/checkout-api` containing "cannot reach dependency".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set environment variable `INVENTORY_URL` to `http://inventory.shop.internal:8080/health`.

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

- 'Connection refused' means the host was found but nothing is listening on that port
- A DNS error and a refused connection point at different mistakes: the name versus the port
- The dependency's own task definition is the source of truth for its port
- Read the error text precisely; it usually narrows the fault to one field

## On real AWS

- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
- The service's Events tab on AWS names the failure (for example 'tasks failed to start'). In the lab, events only ever say the service reached a steady state.
