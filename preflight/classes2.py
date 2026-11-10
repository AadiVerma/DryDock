#!/usr/bin/env python3
"""Second probe: the parts of Floci not tried yet.

Three phases. Phases 2 and 3 restart Floci with a feature switched on, so this
takes about six minutes and leaves the emulator freshly reset at the end.

  1. More failure modes on the normal configuration: service discovery,
     Secrets Manager, environment files, deployment settings, API validation.
  2. Security group enforcement: can a missing inbound rule cut the load
     balancer off from a task?
  3. IAM: task role credentials and policy enforcement. Does an app whose task
     role lacks a permission get AccessDenied?

    ecslab start
    python preflight/classes2.py              # all phases
    python preflight/classes2.py --phases 1

Nothing is faked. Evidence is written to preflight/results/classes2-*.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "preflight"))

import classes as c1                          # noqa: E402
from ecslab import config                     # noqa: E402

ACC, REG = config.ACCOUNT, config.REGION
FLOCI_INSIDE = "http://ecslab-floci:4566"     # how a task container reaches the emulator


def clients():
    import boto3
    from botocore.config import Config
    cfg = Config(retries={"max_attempts": 2}, connect_timeout=5, read_timeout=30)
    names = ("ecs", "elbv2", "ec2", "logs", "iam", "ssm", "secretsmanager", "servicediscovery", "s3", "sts")
    return {n: boto3.client(n, endpoint_url=config.FLOCI_URL, region_name=REG, config=cfg,
                            aws_access_key_id="test", aws_secret_access_key="test") for n in names}


def recreate(env, args):
    """Restart Floci with extra settings. State is in memory, so this is a clean slate."""
    if args.no_recreate:
        return clients()
    from ecslab import cli
    cli._remove_task_containers()
    p = subprocess.run(["docker", "compose", "up", "-d", "--force-recreate"], cwd=ROOT, text=True,
                       env={**os.environ, **env}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        raise RuntimeError("docker compose failed: " + p.stdout[-400:])
    aws = clients()
    deadline = time.time() + 120
    while True:
        try:
            aws["sts"].get_caller_identity()
            break
        except Exception as e:
            try:                               # enforcement may deny even this; any answer means it is up
                if getattr(e, "response", None):
                    break
            except Exception:
                pass
            if time.time() > deadline:
                raise RuntimeError(f"Floci did not come back: {c1.err(e)}")
            time.sleep(2)
    cli._remove_task_containers()
    banner = subprocess.run(["docker", "logs", config.FLOCI_CONTAINER], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT).stdout
    for line in banner.splitlines():
        if "IAM:" in line or "ecurity group" in line or "redential" in line:
            print("    floci: " + line.strip()[-150:])
    return aws


def base(aws):
    aws["ecs"].create_cluster(clusterName=c1.CLUSTER)
    vpc = aws["ec2"].describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subnets = [s["SubnetId"] for s in aws["ec2"].describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["Subnets"]][:2]
    sg = aws["ec2"].describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["SecurityGroups"][0]["GroupId"]
    return vpc, subnets, sg


def run(aws, probes, seconds, net, results, second_step=None):
    vpc, subnets, sg = net
    made = {}
    for p in probes:
        try:
            if p.get("pre"):
                p = {**p, **(p["pre"](aws, net) or {})}
            made[p["name"]] = c1.launch(aws, p, subnets, sg, vpc)
        except Exception as e:
            results[p["name"]] = {"error": c1.err(e)}
    start, done = time.time(), set()
    while time.time() - start < seconds:
        for p in probes:
            m = made.get(p["name"])
            if m and m.get("then_deploy") and time.time() - start > 25 and p["name"] not in done:
                try:
                    aws["ecs"].update_service(cluster=c1.CLUSTER, service=m["service"], taskDefinition=m["then_deploy"])
                except Exception as e:
                    results[p["name"]] = {"error": "second step failed: " + c1.err(e)}
                done.add(p["name"])
        time.sleep(5)
    for p in probes:
        m = made.get(p["name"])
        if m and p["name"] not in results:
            try:
                o = c1.observe(aws, m["service"], m)
                if m.get("then_deploy"):
                    o["onNewRevision"] = o["taskDefinition"] == m["then_deploy"].rsplit("/", 1)[-1]
                results[p["name"]] = o
            except Exception as e:
                results[p["name"]] = {"error": "could not observe: " + c1.err(e)}


def show(probes, results):
    for p in probes:
        o = results.get(p["name"], {"error": "not run"})
        print(f"{p['name']:<24}{c1.verdict(o):<14}{p['models']}")
        pad = " " * 38
        if "error" in o:
            print(pad + o["error"][:230])
            continue
        print(f"{pad}launched {o['launched']}, stopped {o['stopped']}, running {o['running']}/{o['desired']}, "
              f"rollout {o['rollout']}, lastStatus {o['lastStatuses']}")
        if o["stopped"]:
            print(f"{pad}stopCode {o['stopCodes']}  stoppedReason {[r[:200] for r in o['stoppedReasons']]}  exit {o['exitCodes']}")
        if "targets" in o:
            print(f"{pad}targets {o['targets']}")
        if "onNewRevision" in o:
            print(f"{pad}service moved to the new revision: {o['onNewRevision']}; tasks launched in total {o['launched']}")
        if o.get("logs"):
            print(pad + "logs: " + " | ".join(o["logs"])[:240])


def note(results, key, what, fn):
    """A one-off API question: record what the emulator answered."""
    try:
        results[key] = {"question": what, "answer": str(fn())[:300]}
    except Exception as e:
        results[key] = {"question": what, "answer": "REJECTED " + c1.err(e)[:280]}
    print(f"{key:<24}{results[key]['answer'][:180]}\n{'':<24}({what})")


# ---- phase 1 --------------------------------------------------------------- #

def cloud_map(aws, net):
    """An `inventory` service registered in Cloud Map as inventory.shop.internal."""
    sd = aws["servicediscovery"]
    op = sd.create_private_dns_namespace(Name="shop.internal", Vpc=net[0])["OperationId"]
    ns = None
    for _ in range(20):
        o = sd.get_operation(OperationId=op)["Operation"]
        if o.get("Status") == "SUCCESS":
            ns = o["Targets"]["NAMESPACE"]
            break
        time.sleep(1)
    arn = sd.create_service(Name="inventory", NamespaceId=ns,
                            DnsConfig={"DnsRecords": [{"Type": "A", "TTL": 10}]})["Service"]["Arn"]
    return {"service": {"serviceRegistries": [{"registryArn": arn}]}}


def secret_json(aws, net):
    arn = aws["secretsmanager"].create_secret(Name="shop/prod/db", SecretString=json.dumps({"username": "checkout", "password": "pw"}))["ARN"]
    return {"container": {"secrets": [{"name": "DB_PASSWORD", "valueFrom": f"{arn}:password::"}]}}


def secret_json_bad_key(aws, net):
    arn = aws["secretsmanager"].describe_secret(SecretId="shop/prod/db")["ARN"]
    return {"container": {"secrets": [{"name": "DB_PASSWORD", "valueFrom": f"{arn}:passwd::"}]}}


PHASE1 = [
    dict(name="discovery-provider", models="A service registered in Cloud Map (inventory.shop.internal)", pre=cloud_map),
    dict(name="discovery-consumer", models="Another service finds it by DNS name at startup",
         env={"REQUIRE_HTTP": "http://inventory.shop.internal:8080/health"}, delay=30),
    dict(name="sm-json-key", models="Secrets Manager secret, one JSON key injected", pre=secret_json, env={"REQUIRE_ENV": "DB_PASSWORD"}),
    dict(name="sm-json-bad-key", models="Secrets Manager JSON key does not exist: ResourceInitializationError",
         pre=secret_json_bad_key, env={"REQUIRE_ENV": "DB_PASSWORD"}),
    dict(name="sm-missing", models="Secrets Manager secret does not exist: ResourceInitializationError",
         container={"secrets": [{"name": "DB_PASSWORD", "valueFrom": f"arn:aws:secretsmanager:{REG}:{ACC}:secret:shop/prod/nope-AbCdEf"}]}),
    dict(name="envfile-missing", models="environmentFiles points at an S3 object that does not exist",
         container={"environmentFiles": [{"type": "s3", "value": "arn:aws:s3:::shop-config/checkout/prod.env"}]}),
    dict(name="no-exec-role", models="Secrets used but no execution role on the task definition",
         td={"executionRoleArn": None}, ssm={"/shop/prod/x": "1"},
         container={"secrets": [{"name": "X", "valueFrom": f"arn:aws:ssm:{REG}:{ACC}:parameter/shop/prod/x"}]}),
    dict(name="log-group-missing", models="awslogs group does not exist and awslogs-create-group is not set",
         container={"logConfiguration": {"logDriver": "awslogs", "options": {
             "awslogs-group": "/ecs/never-created", "awslogs-region": REG, "awslogs-stream-prefix": "ecs"}}}),
    dict(name="task-memory", models="Task-level memory (512) exceeded with no container limit: killed, exit 137",
         env={"MEMORY_HOG_MB": "800"}),
    dict(name="min100-max100", models="minimumHealthyPercent 100 + maximumPercent 100: a new deployment can never start",
         deploy_over_control=True, env={"APP_BUILD": "2"},
         service={"deploymentConfiguration": {"minimumHealthyPercent": 100, "maximumPercent": 100}}),
    dict(name="lb-wrong-container", models="Service load balancer names a container that is not in the task definition",
         lb={}, lb_container="web"),
    dict(name="lb-instance-target", models="awsvpc task with a target group of type instance", lb={}, tg_type="instance"),
    dict(name="slow-start-grace", models="App needs 40s to start; does anything kill it before it is ready?",
         env={"STARTUP_DELAY_SECONDS": "40"}, lb={}),
]


def phase1(args, results):
    print("\n== Phase 1: more failure modes, normal configuration")
    aws = recreate({}, args)
    net = base(aws)
    run(aws, PHASE1, args.observe, net, results)
    show(PHASE1, results)
    print("\n-- API validation: does Floci reject what AWS rejects?")
    ecs = aws["ecs"]
    logs = {"logDriver": "awslogs", "options": {"awslogs-group": "/probe/x", "awslogs-region": REG, "awslogs-stream-prefix": "ecs"}}
    app = {"name": "app", "image": c1.IMAGE, "portMappings": [{"containerPort": 8080}], "logConfiguration": logs}
    fargate = dict(networkMode="awsvpc", requiresCompatibilities=["FARGATE"], cpu="256", memory="512", executionRoleArn=c1.ROLE)
    note(results, "v-no-essential", "task definition where no container is essential",
         lambda: ecs.register_task_definition(family="v1", containerDefinitions=[{**app, "essential": False}], **fargate)["taskDefinition"]["status"])
    note(results, "v-bridge-fargate", "Fargate task definition with networkMode bridge",
         lambda: ecs.register_task_definition(family="v2", containerDefinitions=[app], **{**fargate, "networkMode": "bridge"})["taskDefinition"]["status"])
    note(results, "v-dup-names", "two containers with the same name",
         lambda: ecs.register_task_definition(family="v3", containerDefinitions=[app, app], **fargate)["taskDefinition"]["status"])
    note(results, "v-hostport", "awsvpc container with hostPort different from containerPort",
         lambda: ecs.register_task_definition(family="v4", containerDefinitions=[{**app, "portMappings": [{"containerPort": 8080, "hostPort": 80}]}], **fargate)["taskDefinition"]["status"])

    def inactive():
        arn = ecs.register_task_definition(family="v5", containerDefinitions=[app], **fargate)["taskDefinition"]["taskDefinitionArn"]
        ecs.deregister_task_definition(taskDefinition=arn)
        return ecs.update_service(cluster=c1.CLUSTER, service="probe-task-memory", taskDefinition=arn)["service"]["taskDefinition"]
    note(results, "v-inactive-taskdef", "update a service to a deregistered (INACTIVE) task definition", inactive)
    note(results, "v-missing-taskdef", "update a service to a revision that does not exist",
         lambda: ecs.update_service(cluster=c1.CLUSTER, service="probe-task-memory", taskDefinition="probe-task-memory:99")["service"]["taskDefinition"])
    note(results, "v-no-subnets", "create an awsvpc service with no network configuration",
         lambda: ecs.create_service(cluster=c1.CLUSTER, serviceName="v6", taskDefinition="probe-task-memory", desiredCount=1,
                                    launchType="FARGATE")["service"]["status"])
    note(results, "v-bad-subnet", "create a service in a subnet that does not exist",
         lambda: ecs.create_service(cluster=c1.CLUSTER, serviceName="v7", taskDefinition="probe-task-memory", desiredCount=1, launchType="FARGATE",
                                    networkConfiguration={"awsvpcConfiguration": {"subnets": ["subnet-0doesnotexist"]}})["service"]["status"])
    note(results, "v-exec", "ECS Exec on a service without enableExecuteCommand",
         lambda: ecs.execute_command(cluster=c1.CLUSTER, interactive=True, command="sh", container="app",
                                     task=ecs.list_tasks(cluster=c1.CLUSTER, serviceName="probe-slow-start-grace")["taskArns"][0])["session"]["sessionId"])


# ---- phase 2 --------------------------------------------------------------- #

def phase2(args, results):
    print("\n== Phase 2: security group enforcement switched on")
    aws = recreate({"ECSLAB_SG_ENFORCEMENT": "true"}, args)
    net = base(aws)
    ec2 = aws["ec2"]

    def group(name, rules):
        gid = ec2.create_security_group(GroupName=name, Description=name, VpcId=net[0])["GroupId"]
        if rules:
            ec2.authorize_security_group_ingress(GroupId=gid, IpPermissions=rules)
        return gid
    try:
        closed = group("probe-closed", [])
        wrong = group("probe-wrong-port", [{"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
        opened = group("probe-open-8080", [{"IpProtocol": "tcp", "FromPort": 8080, "ToPort": 8080, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    except Exception as e:
        print("  could not create security groups: " + c1.err(e))
        results["sg-setup"] = {"error": c1.err(e)}
        return
    probes = [
        dict(name="sg-default", models="Default security group: load balancer should reach the task", lb={}),
        dict(name="sg-open-8080", models="Group allowing 8080 from anywhere: healthy", lb={}, sg=opened),
        dict(name="sg-no-ingress", models="Group with no inbound rules: health checks time out", lb={}, sg=closed),
        dict(name="sg-wrong-port", models="Group allowing only 443: health checks on 8080 time out", lb={}, sg=wrong),
    ]
    run(aws, probes, args.observe, net, results)
    show(probes, results)


# ---- phase 3 --------------------------------------------------------------- #

def phase3(args, results):
    print("\n== Phase 3: task role credentials and IAM enforcement switched on")
    aws = recreate({"ECSLAB_IAM_ENFORCEMENT": "true", "ECSLAB_TASK_ROLE_CREDENTIALS": "true"}, args)
    note(results, "iam-admin-identity", "who the lab's own test/test credentials are, with enforcement on",
         lambda: aws["sts"].get_caller_identity()["Arn"])
    note(results, "iam-admin-ecs", "can the lab's own credentials still call ECS with enforcement on",
         lambda: aws["ecs"].list_clusters()["clusterArns"])
    try:
        net = base(aws)
        trust = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole",
                                                                    "Principal": {"Service": "ecs-tasks.amazonaws.com"}}]})
        iam = aws["iam"]
        for name in ("ecsTaskExecutionRole", "checkout-task-role", "checkout-task-role-no-s3"):
            iam.create_role(RoleName=name, AssumeRolePolicyDocument=trust)
        iam.put_role_policy(RoleName="checkout-task-role", PolicyName="receipts", PolicyDocument=json.dumps({
            "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetObject"],
                                                    "Resource": ["arn:aws:s3:::shop-receipts", "arn:aws:s3:::shop-receipts/*"]}]}))
        iam.put_role_policy(RoleName="checkout-task-role-no-s3", PolicyName="logs-only", PolicyDocument=json.dumps({
            "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "logs:*", "Resource": "*"}]}))
        aws["s3"].create_bucket(Bucket="shop-receipts")
    except Exception as e:
        print("  setup failed with enforcement on: " + c1.err(e))
        results["iam-setup"] = {"error": c1.err(e)}
        return
    env = {"RECEIPTS_BUCKET": "shop-receipts", "AWS_ENDPOINT_URL": FLOCI_INSIDE, "AWS_REGION": REG}
    role = lambda n: f"arn:aws:iam::{ACC}:role/{n}"   # noqa: E731
    probes = [
        dict(name="role-allowed", models="Task role allows s3:ListBucket on the bucket: app starts", env=env, td={"taskRoleArn": role("checkout-task-role")}),
        dict(name="role-denied", models="Task role has no S3 permission: app gets AccessDenied and exits", env=env,
             td={"taskRoleArn": role("checkout-task-role-no-s3")}),
        dict(name="role-absent", models="No task role at all: app cannot find credentials", env=env),
        dict(name="role-missing", models="taskRoleArn names a role that does not exist", env=env, td={"taskRoleArn": role("checkout-task-role-typo")}),
    ]
    run(aws, probes, args.observe, net, results)
    show(probes, results)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phases", default="1,2,3")
    ap.add_argument("--observe", type=int, default=75, help="seconds to watch each phase (default 75)")
    ap.add_argument("--no-recreate", action="store_true", help="do not restart Floci between phases (for testing the script)")
    args = ap.parse_args()
    results = {}
    try:
        for n, fn in (("1", phase1), ("2", phase2), ("3", phase3)):
            if n in args.phases.split(","):
                try:
                    fn(args, results)
                except Exception as e:
                    print(f"  phase {n} aborted: {c1.err(e)}")
                    results[f"phase{n}"] = {"error": c1.err(e)}
    finally:
        out_dir = ROOT / "preflight" / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"classes2-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
        out.write_text(json.dumps(c1.strip(results), indent=2, default=str))
        print(f"\nEvidence: {out.relative_to(ROOT)}")
        if not args.no_recreate:
            try:
                recreate({}, args)
                print("Floci restarted with its normal settings.")
            except Exception as e:
                print("Could not restore Floci; run `ecslab reset`: " + c1.err(e))


if __name__ == "__main__":
    main()
