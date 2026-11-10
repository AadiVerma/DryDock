# ecs-028-three-faults solution (spoilers)

**The long afternoon**, difficulty 5/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

## What you should see while it is broken

- Stopped tasks with stop code `TaskFailedToStart`, stoppedReason containing "pull access denied".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set environment variable `PORT` to `8080`; in the app container, set the image to `ecslab/checkout-api:1.5.0`.

   ```bash
   aws ecs describe-task-definition --task-definition checkout-api:8 --query taskDefinition > td.json
   # edit td.json as above, keep only the registerable fields, then:
   aws ecs register-task-definition --cli-input-json file://td.json
   ```

2. Register a new revision based on checkout-api:<the revision you just registered>: in container `migrate`, set environment variable `MIGRATIONS_DATABASE_URL` to `postgres://checkout@db.shop.internal:5432/checkout`.

   ```bash
   aws ecs describe-task-definition --task-definition checkout-api:<the revision you just registered> --query taskDefinition > td.json
   # edit td.json as above, keep only the registerable fields, then:
   aws ecs register-task-definition --cli-input-json file://td.json
   ```

3. Update service checkout-api: deploy checkout-api:<the revision you just registered>.

   ```bash
   aws ecs update-service --cluster shop-prod --service checkout-api --task-definition checkout-api:<the revision you just registered>
   ```

## What does not count as a fix

- Scale to zero.

## What it teaches

- A bad deployment can fail in stages, each fix revealing the next fault
- The last fault may be silent: tasks running, deployment complete, and no traffic getting through
- Finish by checking target health, not the service's running count
- Verify the outcome customers see, not the absence of errors

## On real AWS

- On AWS, ECS stops and replaces tasks whose targets fail the load balancer health check, with 'failed ELB health checks' in stoppedReason and the service events. The lab leaves those tasks running.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
- With the deployment circuit breaker enabled, AWS marks the deployment FAILED and rolls back by itself. The lab always reports the rollout as completed, so never trust that field here.
