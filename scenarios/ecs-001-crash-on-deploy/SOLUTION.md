# ecs-001 solution (spoilers)

Use this to verify the lab end to end. Run it in a terminal with the three
`export` lines from `ecslab run`.

```bash
# 1. Do what the brief says. This is the trap.
aws ecs update-service --cluster shop-prod --service checkout-api --task-definition checkout-api:8

# 2. The service looks fine: 2 of 2 running, on revision 8. It is not fine. The running
#    tasks are still revision 7's (list-tasks + describe-tasks shows their taskDefinitionArn).
aws ecs describe-services --cluster shop-prod --services checkout-api \
  --query 'services[0].{desired:desiredCount,running:runningCount,taskDef:taskDefinition}'

# 3. A stopped task says it crashed, with exit code 1.
TASK=$(aws ecs list-tasks --cluster shop-prod --desired-status STOPPED --query 'taskArns[0]' --output text)
aws ecs describe-tasks --cluster shop-prod --tasks $TASK \
  --query 'tasks[0].{reason:stoppedReason,code:stopCode,exit:containers[0].exitCode}'

# 4. The container says why. The stream name is <prefix>/<container>/<task id>,
#    so the stopped task from step 3 leads straight to its own log.
aws logs get-log-events --log-group-name /ecs/checkout-api \
  --log-stream-name "ecs/app/${TASK##*/}" --query 'events[].message' --output text
# -> FATAL: missing required environment variable PAYMENTS_API_URL

# 5. Register revision 9: revision 8 plus the missing variable.
aws ecs describe-task-definition --task-definition checkout-api:8 --query taskDefinition > td.json
python3 - <<'PY'
import json
td = json.load(open("td.json"))
td["containerDefinitions"][0]["environment"].append(
    {"name": "PAYMENTS_API_URL", "value": "https://payments.shop.internal"})
keep = ("family", "networkMode", "requiresCompatibilities", "cpu", "memory",
        "executionRoleArn", "containerDefinitions")
json.dump({k: td[k] for k in keep if k in td}, open("td9.json", "w"))
PY
aws ecs register-task-definition --cli-input-json file://td9.json --query taskDefinition.revision

# 6. Deploy it and check.
aws ecs update-service --cluster shop-prod --service checkout-api --task-definition checkout-api:9
ecslab check
```

Also worth trying, each should be rejected or redirected by `ecslab check`:

- `--task-definition checkout-api:7` (rollback: accepted, then asks for the real fix)
- `--desired-count 0` (forbidden)
- `aws elbv2 modify-target-group ... --matcher HttpCode=200-599` (forbidden)
