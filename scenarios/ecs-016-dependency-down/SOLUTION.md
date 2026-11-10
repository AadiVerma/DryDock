# ecs-016-dependency-down solution (spoilers)

**Somebody else's outage**, difficulty 3/5.

## The task

checkout-api started crash-looping after a routine restart ten minutes ago.
Nobody has changed checkout. Find out why and get it serving again.

checkout-api runs in the shop-prod cluster behind the checkout-tg target group.
It calls the inventory service, which another team runs in the same cluster.

## What was done to the environment

- Update service inventory: set desired tasks to 0.
- Update service checkout-api: force a new deployment.

## What you should see while it is broken

- Stopped tasks with stoppedReason containing "Essential container", exit code 1.
- A log line in `/ecs/checkout-api` containing "cannot reach dependency".

## The fix

1. Update service inventory: set desired tasks to 1.

   ```bash
   aws ecs update-service --cluster shop-prod --service inventory --desired-count 1
   ```

## What it teaches

- The service that is crashing is not always the service that is broken
- A dependency that is down removes its DNS name, so callers see a name resolution failure
- Read the whole cluster, not just the service in the alert
- Fix the dependency; do not remove the dependency check to make the symptom go away

## On real AWS

- Cloud Map DNS answers are cached for their TTL on AWS, so a recovered or renamed dependency can take up to a minute to resolve.
- On AWS the other team's service would show its own events and alarms; in an incident, ask before you scale someone else's service.
