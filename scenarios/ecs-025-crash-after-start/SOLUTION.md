# ecs-025-crash-after-start solution (spoilers)

**Fine for fifteen seconds**, difficulty 4/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

## What you should see while it is broken

- Stopped tasks with stoppedReason containing "Essential container", exit code 3.
- A log line in `/ecs/checkout-api` containing "invalid schedule".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set environment variable `REPORT_SCHEDULE` to `*/5 * * * *`.

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

- A service can report all tasks running while each one dies seconds later: check task age and the stopped list
- A crash after startup leaves the startup log lines looking normal; the error comes later in the stream
- The exit code distinguishes this failure from a startup failure
- A service is stable when the same tasks are still running a minute later, not when the count is right

## On real AWS

- AWS deregisters the targets of stopped tasks. The lab can leave them in the target group as unhealthy, so count healthy targets rather than expecting an empty list.
- On AWS, ECS stops and replaces tasks whose targets fail the load balancer health check, with 'failed ELB health checks' in stoppedReason and the service events. The lab leaves those tasks running.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
