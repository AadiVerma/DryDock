# ecs-011-health-check-path solution (spoilers)

**Nothing was deployed**, difficulty 2/5.

## The task

Customers started getting 502 errors from checkout a few minutes ago.
Nothing was deployed today. Find out why and fix it.

checkout-api runs in the shop-prod cluster behind the checkout-tg target group.

## What was done to the environment

- Change the health check on target group checkout-tg: path = /healthz.

## What you should see while it is broken

- Unhealthy targets in `checkout-tg`.

## The fix

1. Change the health check on target group checkout-tg: path = /health.

   ```bash
   aws elbv2 modify-target-group --target-group-arn <arn of checkout-tg> --health-check-path /health
   ```

## What does not count as a fix

- Accept any response as healthy.

## What it teaches

- A service can be fully running while the load balancer refuses to send it traffic
- Target.ResponseCodeMismatch means the app answered, but not with the status code the health check expects
- Health check settings live on the target group, not in the task definition or the service
- Changing what counts as healthy hides the problem; pointing the check at the right endpoint fixes it

## On real AWS

- On AWS, ECS stops and replaces tasks whose targets fail the load balancer health check, with 'failed ELB health checks' in stoppedReason and the service events. The lab leaves those tasks running.
- CloudTrail on AWS records who made the change and when, which is usually the fastest way to find 'what changed'.
