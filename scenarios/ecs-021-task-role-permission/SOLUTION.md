# ecs-021-task-role-permission solution (spoilers)

**Access denied**, difficulty 3/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

The new version stores receipts in the shop-receipts S3 bucket.

## What you should see while it is broken

- Stopped tasks with stoppedReason containing "Essential container", exit code 1.
- A log line in `/ecs/checkout-api` containing "AccessDenied".

## The fix

1. Give role checkout-task-role an inline policy `receipts` with exactly the permission that was denied.

   ```bash
   aws iam put-role-policy --role-name checkout-task-role --policy-name receipts --policy-document '{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::shop-receipts"}]}'
   ```

2. Update service checkout-api: deploy checkout-api:<the revision you just registered>.

   ```bash
   aws ecs update-service --cluster shop-prod --service checkout-api --task-definition checkout-api:<the revision you just registered>
   ```

## What does not count as a fix

- Scale to zero.
- Grant the role everything.

## What it teaches

- An application in a task calls AWS as the task role, not as you
- AccessDenied names the action that was refused and the identity that was refused
- The fix belongs in IAM, on the task role; nothing in the task definition is wrong
- Grant the specific action on the specific resource; a wildcard policy is a shortcut, not a fix

## On real AWS

- On AWS the identity in the error is an assumed-role ARN ending in the task ID, and the denied call is recorded in CloudTrail.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
