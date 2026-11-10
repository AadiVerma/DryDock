# ecs-035-no-role-wrong-policy solution (spoilers)

**No one, then not allowed**, difficulty 5/5.

## The task

The team pushed a new image. Deploy checkout-api:8 to production.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy. Revision 8 is already registered.

The new version stores receipts in the shop-receipts S3 bucket.

## What you should see while it is broken

- Stopped tasks with stoppedReason containing "Essential container", exit code 1.
- A log line in `/ecs/checkout-api` containing "Unable to locate credentials".

## The fix

1. Give role checkout-task-role an inline policy `receipts` with exactly the permission that was denied.

   ```bash
   aws iam put-role-policy --role-name checkout-task-role --policy-name receipts --policy-document '{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::shop-receipts"}]}'
   ```

2. Register a new revision based on checkout-api:8: at task level, set `taskRoleArn` to `arn:aws:iam::000000000000:role/checkout-task-role`.

   ```bash
   aws ecs describe-task-definition --task-definition checkout-api:8 --query taskDefinition > td.json
   # edit td.json as above, keep only the registerable fields, then:
   aws ecs register-task-definition --cli-input-json file://td.json
   ```

3. Update service checkout-api: deploy checkout-api:<the revision you just registered>.

   ```bash
   aws ecs update-service --cluster shop-prod --service checkout-api --task-definition checkout-api:<the revision you just registered>
   ```

## What does not count as a fix

- Scale to zero.
- Grant the role everything.

## What it teaches

- 'Unable to locate credentials' and 'AccessDenied' are different problems: no identity, and an identity without permission
- Fixing who you are does not fix what you are allowed to do
- One fix is in the task definition (taskRoleArn), the other is in IAM (the role's policy)
- Grant the specific action on the specific resource; a wildcard policy is a shortcut, not a fix

## On real AWS

- On AWS the identity in the error is an assumed-role ARN ending in the task ID, and the denied call is recorded in CloudTrail.
- A crash-looping service is slowed down on AWS: ECS waits longer between launches after repeated failures. The lab relaunches every few seconds.
