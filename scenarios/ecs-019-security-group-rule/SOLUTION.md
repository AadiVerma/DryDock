# ecs-019-security-group-rule solution (spoilers)

**A tidy-up too far**, difficulty 3/5.

## The task

Checkout has been returning 504 errors since a security review this morning.
The tasks are running and nothing was deployed.

checkout-api runs in the shop-prod cluster behind the checkout-tg target group.
Its tasks are in the checkout-sg security group.

## What was done to the environment

- Make the inbound rules of security group checkout-sg exactly: TCP 8443 from 0.0.0.0/0.

## What you should see while it is broken

- Unhealthy targets in `checkout-tg`.

## The fix

1. Make the inbound rules of security group checkout-sg exactly: TCP 8080 from 0.0.0.0/0.

   ```bash
   aws ec2 revoke-security-group-ingress --group-id <id of checkout-sg> --ip-permissions <the wrong rule>
   aws ec2 authorize-security-group-ingress --group-id <id of checkout-sg> --protocol tcp --port 8080 --cidr 0.0.0.0/0
   ```

## What does not count as a fix

- Open every port.

## What it teaches

- Target.Timeout means the health check got no answer: suspect the network path, not the application
- A security group must allow inbound traffic on the container port, or the load balancer cannot reach the task
- Timeouts point at security groups and routing; wrong responses point at the application
- Allow the one port the application needs, from where it needs it, and nothing more

## On real AWS

- On AWS the load balancer has a security group of its own, and the usual rule allows the task port from that group rather than from 0.0.0.0/0.
- On AWS, ECS stops and replaces tasks whose targets fail the load balancer health check, with 'failed ELB health checks' in stoppedReason and the service events. The lab leaves those tasks running.
