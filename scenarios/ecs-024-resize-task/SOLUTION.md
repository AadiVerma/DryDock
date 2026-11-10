# ecs-024-resize-task solution (spoilers)

**More memory, please**, difficulty 2/5.

## The task

Checkout needs more headroom for the sale. Give each task 4 GB of memory,
keep it on Fargate, and deploy it. Change nothing else.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy.

## The fix

1. Register a new revision based on checkout-api:7: at task level, set `cpu` to `512`; at task level, set `memory` to `4096`.

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

- Fargate task sizes come in fixed CPU and memory pairs; you cannot change one freely
- An invalid pair is refused when the task definition is registered, before anything is deployed
- 0.25 vCPU tops out at 2 GB; 4 GB needs at least 0.5 vCPU
- A task definition change is only live once the service is updated to the new revision

## On real AWS

- The Fargate CPU and memory table is the same on AWS, and the refusal comes at registration there too.
- On AWS a deployment replaces tasks gradually, governed by minimumHealthyPercent and maximumPercent. The lab replaces them all at once.
