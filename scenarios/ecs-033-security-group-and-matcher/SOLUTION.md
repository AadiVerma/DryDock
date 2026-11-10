# ecs-033-security-group-and-matcher solution (spoilers)

**Reviewed twice**, difficulty 4/5.

## The task

Checkout has been failing since this morning's security review, which also
"standardised" some load balancer settings. The tasks are running and
nothing was deployed.

checkout-api runs in the shop-prod cluster behind the checkout-tg target group.
Its tasks are in the checkout-sg security group.

## What was done to the environment

- Make the inbound rules of security group checkout-sg exactly: TCP 8443 from 0.0.0.0/0.
- Change the health check on target group checkout-tg: matcher = 204.

## What you should see while it is broken

- Unhealthy targets in `checkout-tg`.

## The fix

1. Make the inbound rules of security group checkout-sg exactly: TCP 8080 from 0.0.0.0/0.

   ```bash
   aws ec2 revoke-security-group-ingress --group-id <id of checkout-sg> --ip-permissions <the wrong rule>
   aws ec2 authorize-security-group-ingress --group-id <id of checkout-sg> --protocol tcp --port 8080 --cidr 0.0.0.0/0
   ```

2. Change the health check on target group checkout-tg: matcher = 200.

   ```bash
   aws elbv2 modify-target-group --target-group-arn <arn of checkout-tg> --matcher HttpCode=200
   ```

## What does not count as a fix

- Open every port.

## What it teaches

- The reason code on an unhealthy target changes as you fix things; read it again after every change
- Target.Timeout is the network path; Target.ResponseCodeMismatch is the health check's expectations
- The fault nearest the network hides every fault behind it
- Unhealthy before and unhealthy after does not mean your fix did nothing

## On real AWS

- On AWS the load balancer has a security group of its own, and the usual rule allows the task port from that group rather than from 0.0.0.0/0.
- On AWS, ECS stops and replaces tasks whose targets fail the load balancer health check, with 'failed ELB health checks' in stoppedReason and the service events. The lab leaves those tasks running.
