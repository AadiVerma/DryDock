# ecs-029-scale-out solution (spoilers)

**Four for the sale**, difficulty 1/5.

## The task

The sale starts tonight. Run four checkout tasks instead of two, and make
sure all four are taking traffic before you call it done.

checkout-api runs in the shop-prod cluster behind the checkout-tg target
group. Revision 7 is live and healthy.

## The fix

1. Update service checkout-api: set desired tasks to 4.

   ```bash
   aws ecs update-service --cluster shop-prod --service checkout-api --desired-count 4
   ```

## What does not count as a fix

- Scale to zero.

## What it teaches

- The number of tasks is the service's desiredCount; the task definition does not know how many copies run
- Scaling out starts new tasks from the task definition the service already uses
- A new task takes traffic only once its target is healthy in the target group, which is later than RUNNING
- Done means the load balancer agrees, not that the API call returned

## On real AWS

- On AWS, scaling out can also fail for lack of capacity or subnet IP addresses; the service events say so.
- Production services usually scale through Application Auto Scaling, which will undo a manual change if its own target disagrees.
