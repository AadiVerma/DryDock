# ecs-010-wrong-port solution (spoilers)

**Green deploy, dead site**, difficulty 3/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

After you deploy, check that customers can still reach checkout.

## What you should see while it is broken

- Unhealthy targets in `checkout-tg`.
- A log line in `/ecs/checkout-api` containing "listening on port 9000".

## The fix

1. Register a new revision based on checkout-api:8: in the app container, set environment variable `PORT` to `8080`.

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

- Running tasks do not mean a working service: the load balancer decides whether traffic can reach them
- describe-target-health shows each target's state and the reason it is failing
- The containerPort in the task definition must be the port the application really listens on
- The application's own startup log is the quickest way to see what it actually bound to

## On real AWS

- On AWS, ECS stops and replaces tasks whose targets fail the load balancer health check, with 'failed ELB health checks' in stoppedReason and the service events. The lab leaves those tasks running.
- Because of that, on AWS this deployment would not look green for long: the new tasks would be cycled and the events would say why.
