# ecs-022-no-task-role solution (spoilers)

**Who am I**, difficulty 3/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

The new version stores receipts in the shop-receipts S3 bucket.

## What you should see while it is broken

- Stopped tasks with stoppedReason containing "Essential container", exit code 1.
- A log line in `/ecs/checkout-api` containing "Unable to locate credentials".

## The fix

1. Register a new revision based on checkout-api:8: at task level, set `taskRoleArn` to `arn:aws:iam::000000000000:role/checkout-task-role`.

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

- 'Unable to locate credentials' means there is no identity at all, which is different from AccessDenied
- A task gets AWS credentials from its task role; without taskRoleArn there are none
- The task role and the execution role are different: one is for the app, the other is for ECS itself
- taskRoleArn is a task-level field, easy to lose when a task definition is rewritten

## On real AWS

- On AWS, an app with no task role on EC2-backed ECS can silently fall back to the instance's role. On Fargate there is no fallback, as here.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
