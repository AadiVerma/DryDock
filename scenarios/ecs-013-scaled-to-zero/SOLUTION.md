# ecs-013-scaled-to-zero solution (spoilers)

**Where did everybody go**, difficulty 1/5.

## The task

Checkout is completely down. Every request fails. Nothing was deployed.

checkout-api runs in the shop-prod cluster behind the checkout-tg target group.
Production needs two tasks.

## What was done to the environment

- Update service checkout-api: set desired tasks to 0.

## What you should see while it is broken


## The fix

1. Update service checkout-api: set desired tasks to 2.

   ```bash
   aws ecs update-service --cluster shop-prod --service checkout-api --desired-count 2
   ```

## What it teaches

- Read desiredCount before anything else: a service at zero is not broken, it has been told to run nothing
- Tasks stopped by a scale-in carry a scheduler stop code, not an application error
- No targets in a target group can simply mean no tasks exist to register
- The fastest incidents to fix are the ones where you read the service before you read the logs

## On real AWS

- The service events on AWS record the change in desired count.
- CloudTrail on AWS records who made the change and when, which is usually the fastest way to find 'what changed'.
