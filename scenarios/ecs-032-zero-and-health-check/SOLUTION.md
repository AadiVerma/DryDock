# ecs-032-zero-and-health-check solution (spoilers)

**Back up, still down**, difficulty 3/5.

## The task

Checkout is completely down after last night's maintenance window. Nothing
was deployed. Get it serving customers again.

checkout-api runs in the shop-prod cluster behind the checkout-tg target group.
Production needs two tasks.

## What was done to the environment

- Change the health check on target group checkout-tg: path = /healthz.
- Update service checkout-api: set desired tasks to 0.

## What you should see while it is broken


## The fix

1. Update service checkout-api: set desired tasks to 2.

   ```bash
   aws ecs update-service --cluster shop-prod --service checkout-api --desired-count 2
   ```

2. Change the health check on target group checkout-tg: path = /health.

   ```bash
   aws elbv2 modify-target-group --target-group-arn <arn of checkout-tg> --health-check-path /health
   ```

## What does not count as a fix

- Accept any response as healthy.

## What it teaches

- Bringing the tasks back is not the same as bringing the service back
- A fault that was hidden while nothing was running only shows once the first fault is fixed
- Always finish on target health: it is the view your customers get
- Two unrelated changes in one maintenance window give you two unrelated faults

## On real AWS

- On AWS, ECS stops and replaces tasks whose targets fail the load balancer health check, with 'failed ELB health checks' in stoppedReason and the service events. The lab leaves those tasks running.
- CloudTrail on AWS records who made the change and when, which is usually the fastest way to find 'what changed'.
