# ecs-012-health-check-code solution (spoilers)

**Healthy, apparently not**, difficulty 2/5.

## The task

Checkout has been returning 502 errors since an infrastructure change this
morning. The application team says nothing changed on their side.

checkout-api runs in the shop-prod cluster behind the checkout-tg target group.

## What was done to the environment

- Change the health check on target group checkout-tg: matcher = 204.

## What you should see while it is broken

- Unhealthy targets in `checkout-tg`.

## The fix

1. Change the health check on target group checkout-tg: matcher = 200.

   ```bash
   aws elbv2 modify-target-group --target-group-arn <arn of checkout-tg> --matcher HttpCode=200
   ```

## What does not count as a fix

- Point the health check somewhere else.

## What it teaches

- Target.ResponseCodeMismatch means the health endpoint answered with a code the matcher does not accept
- The matcher (success codes) is part of the target group's health check configuration
- The application's access log shows the health check requests and the status it returned
- Fix the check to match what a healthy app returns; do not move the check to an endpoint that happens to pass

## On real AWS

- On AWS, ECS stops and replaces tasks whose targets fail the load balancer health check, with 'failed ELB health checks' in stoppedReason and the service events. The lab leaves those tasks running.
- CloudTrail on AWS records who made the change and when, which is usually the fastest way to find 'what changed'.
