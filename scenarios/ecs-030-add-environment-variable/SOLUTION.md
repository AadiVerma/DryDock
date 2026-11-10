# ecs-030-add-environment-variable solution (spoilers)

**Switch it on**, difficulty 2/5.

## The task

Product wants gift cards live. The application already supports them behind
a flag: set the environment variable FEATURE_GIFT_CARDS to on for checkout
and deploy it. Change nothing else.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy.

## The fix

1. Register a new revision based on checkout-api:7: in the app container, set environment variable `FEATURE_GIFT_CARDS` to `on`.

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

- A task definition revision is immutable: a change means a new revision copied from the old one
- Registering a revision deploys nothing; the service must be updated to the new revision
- Copy the last good revision and change one thing, so anything that breaks has one possible cause
- Running tasks keep their old environment until they are replaced by the deployment

## On real AWS

- On AWS a deployment replaces tasks gradually, governed by minimumHealthyPercent and maximumPercent. The lab replaces them all at once.
- Use 'aws ecs wait services-stable' on AWS to block until the deployment has finished.
