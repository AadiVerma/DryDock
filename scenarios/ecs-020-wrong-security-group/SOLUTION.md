# ecs-020-wrong-security-group solution (spoilers)

**In the wrong group**, difficulty 3/5.

## The task

Checkout stopped answering after a network change this afternoon. The tasks
were replaced as part of that change and the new ones are running.

checkout-api runs in the shop-prod cluster behind the checkout-tg target group.
Its tasks belong in the checkout-sg security group.

## What was done to the environment

- Update service checkout-api: put the tasks in security group default.

## What you should see while it is broken

- Unhealthy targets in `checkout-tg`.

## The fix

1. Update service checkout-api: put the tasks in security group checkout-sg.

   ```bash
   aws ecs update-service --cluster shop-prod --service checkout-api --network-configuration 'awsvpcConfiguration={subnets=[<same subnets>],securityGroups=[<group id>],assignPublicIp=ENABLED}'
   ```

## What does not count as a fix

- Open the default group to everything.

## What it teaches

- A service's security groups are part of its network configuration, set on the service, not the task definition
- Correct rules on the right group do nothing if the tasks are in a different group
- Changing a service's network configuration replaces its tasks
- Put the tasks back in the right group; do not widen the wrong one until it works

## On real AWS

- On AWS the load balancer has a security group of its own, and the usual rule allows the task port from that group rather than from 0.0.0.0/0.
- On AWS, ECS stops and replaces tasks whose targets fail the load balancer health check, with 'failed ELB health checks' in stoppedReason and the service events. The lab leaves those tasks running.
