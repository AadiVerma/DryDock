"""Build a scenario's starting state from seed.yaml. Idempotent."""
from __future__ import annotations

import json
import time

from botocore.exceptions import ClientError

from .config import ACCOUNT, REGION


class SeedError(RuntimeError):
    pass


def _logs(td, prefix="ecs"):
    return {"logDriver": "awslogs", "options": {
        "awslogs-group": td.get("log_group", f"/ecs/{td['family']}"), "awslogs-region": REGION,
        "awslogs-stream-prefix": prefix, "awslogs-create-group": "true"}}


def container_definitions(td: dict, rev: dict) -> list:
    """The app container, with any extra fields from `container:`, plus `sidecars:`."""
    app = container_definition(td, rev)
    app.update(rev.get("container") or {})
    out = [app]
    for side in rev.get("sidecars") or []:
        side = dict(side)
        env = side.pop("environment", None) or {}
        out.append({"image": rev["image"], "essential": False, "logConfiguration": _logs(td),
                    "environment": [{"name": k, "value": str(v)} for k, v in env.items()], **side})
    return out


def container_definition(td: dict, rev: dict) -> dict:
    return {
        "name": td.get("container", "app"),
        "image": rev["image"],
        "essential": True,
        "portMappings": [{"containerPort": td.get("container_port", 8080), "protocol": "tcp"}],
        "environment": [{"name": k, "value": str(v)} for k, v in (rev.get("environment") or {}).items()],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": td.get("log_group", f"/ecs/{td['family']}"),
                "awslogs-region": REGION,
                "awslogs-stream-prefix": "ecs",
                "awslogs-create-group": "true",
            },
        },
    }


def resolve(aws, obj):
    """Replace ${secret:NAME} with that Secrets Manager secret's ARN, anywhere in obj.
    (The ARN ends in a random suffix, so a scenario file cannot know it in advance.)"""
    if isinstance(obj, dict):
        return {k: resolve(aws, v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve(aws, v) for v in obj]
    if isinstance(obj, str) and "${secret:" in obj:
        name = obj.split("${secret:", 1)[1].split("}", 1)[0]
        arn = aws["secretsmanager"].describe_secret(SecretId=name)["ARN"]
        return obj.replace("${secret:" + name + "}", arn)
    return obj


def security_group_id(aws, name):
    found = aws["ec2"].describe_security_groups(Filters=[{"Name": "group-name", "Values": [name]}])["SecurityGroups"]
    if not found:
        raise SeedError(f"security group {name} does not exist")
    return found[0]["GroupId"]


def set_ingress(aws, group_id, rules):
    """Make the group's inbound rules exactly `rules` ([{port, cidr}] or {from_port, to_port, protocol})."""
    ec2 = aws["ec2"]
    current = ec2.describe_security_groups(GroupIds=[group_id])["SecurityGroups"][0].get("IpPermissions", [])
    if current:
        # Keep port 0: it is a real value (a rule from port 0 upwards), not "missing".
        clean = [{k: v for k, v in p.items() if k in ("IpProtocol", "FromPort", "ToPort", "IpRanges", "UserIdGroupPairs")
                  and v is not None and v != []} for p in current]
        ec2.revoke_security_group_ingress(GroupId=group_id, IpPermissions=clean)
    wanted = []
    for r in rules or []:
        perm = {"IpProtocol": str(r.get("protocol", "tcp")), "IpRanges": [{"CidrIp": r.get("cidr", "0.0.0.0/0")}]}
        if perm["IpProtocol"] != "-1":
            perm.update(FromPort=int(r.get("from_port", r.get("port"))), ToPort=int(r.get("to_port", r.get("port"))))
        wanted.append(perm)
    if wanted:
        ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=wanted)


def _seed_security_group(aws, spec, vpc):
    ec2 = aws["ec2"]
    found = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [spec["name"]]}])["SecurityGroups"]
    gid = found[0]["GroupId"] if found else ec2.create_security_group(
        GroupName=spec["name"], Description=spec.get("description", spec["name"]), VpcId=vpc)["GroupId"]
    set_ingress(aws, gid, spec.get("ingress"))
    return gid


def _seed_iam(aws, spec):
    trust = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole",
                                                                "Principal": {"Service": "ecs-tasks.amazonaws.com"}}]})
    for bucket in spec.get("s3_buckets") or []:
        try:
            aws["s3"].create_bucket(Bucket=bucket)
        except ClientError:
            pass
    for role in spec.get("iam_roles") or []:
        try:
            aws["iam"].create_role(RoleName=role["name"], AssumeRolePolicyDocument=trust)
        except ClientError:
            pass
        try:
            for name in aws["iam"].list_role_policies(RoleName=role["name"]).get("PolicyNames", []):
                aws["iam"].delete_role_policy(RoleName=role["name"], PolicyName=name)
        except ClientError:
            pass
        for name, statements in (role.get("policies") or {}).items():
            aws["iam"].put_role_policy(RoleName=role["name"], PolicyName=name,
                                       PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": statements}))
    for name, value in (spec.get("secrets") or {}).items():
        body = value if isinstance(value, str) else json.dumps(value)
        try:
            aws["secretsmanager"].create_secret(Name=name, SecretString=body)
        except ClientError:
            aws["secretsmanager"].put_secret_value(SecretId=name, SecretString=body)


def _seed_extra_service(aws, cluster, svc, vpc, subnets, sg, sleep, timeout):
    """A supporting service with no load balancer, optionally registered in Cloud Map."""
    ecs, name = aws["ecs"], svc["name"]
    registries = []
    if svc.get("discovery"):
        sd, d = aws["servicediscovery"], svc["discovery"]
        ns = next((n["Id"] for n in sd.list_namespaces().get("Namespaces", []) if n["Name"] == d["namespace"]), None)
        if not ns:
            op = sd.create_private_dns_namespace(Name=d["namespace"], Vpc=vpc)["OperationId"]
            for _ in range(30):
                o = sd.get_operation(OperationId=op)["Operation"]
                if o.get("Status") == "SUCCESS":
                    ns = o["Targets"]["NAMESPACE"]
                    break
                sleep(1)
        if not ns:
            raise SeedError(f"could not create the {d['namespace']} namespace")
        existing = [s for s in sd.list_services(Filters=[{"Name": "NAMESPACE_ID", "Values": [ns]}]).get("Services", [])
                    if s["Name"] == d["name"]]
        arn = existing[0]["Arn"] if existing else sd.create_service(
            Name=d["name"], NamespaceId=ns, DnsConfig={"DnsRecords": [{"Type": "A", "TTL": 10}]})["Service"]["Arn"]
        registries = [{"registryArn": arn}]
    td = {"family": name, "container": "app", "container_port": 8080, "log_group": f"/ecs/{name}"}
    if not _family_arns(ecs, name):
        ecs.register_task_definition(
            family=name, networkMode="awsvpc", requiresCompatibilities=["FARGATE"], cpu="256", memory="512",
            executionRoleArn=f"arn:aws:iam::{ACCOUNT}:role/ecsTaskExecutionRole",
            containerDefinitions=container_definitions(td, {"image": svc["image"], "environment": svc.get("environment")}))
    active = [s for s in ecs.describe_services(cluster=cluster, services=[name]).get("services", []) if s.get("status") == "ACTIVE"]
    if active:
        ecs.update_service(cluster=cluster, service=name, desiredCount=svc.get("desired_count", 1))
    else:
        ecs.create_service(cluster=cluster, serviceName=name, taskDefinition=name, desiredCount=svc.get("desired_count", 1),
                           launchType="FARGATE", serviceRegistries=registries,
                           networkConfiguration={"awsvpcConfiguration": {"subnets": subnets, "securityGroups": [sg],
                                                                         "assignPublicIp": "ENABLED"}})
    deadline = time.time() + timeout
    while True:
        s = ecs.describe_services(cluster=cluster, services=[name])["services"][0]
        if s.get("runningCount") == svc.get("desired_count", 1):
            return
        if time.time() >= deadline:
            raise SeedError(f"supporting service {name} never started")
        sleep(3)


def _network(aws):
    ec2 = aws["ec2"]
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise SeedError("the emulator has no default VPC")
    vpc = vpcs[0]["VpcId"]
    subnets = [s["SubnetId"] for s in
               ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["Subnets"]][:2]
    sgs = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["SecurityGroups"]
    sgs = [g for g in sgs if g.get("GroupName") == "default"] or sgs
    if len(subnets) < 2 or not sgs:
        raise SeedError("the default VPC needs two subnets and a security group")
    return vpc, subnets, sgs[0]["GroupId"]


def _family_arns(ecs, family):
    arns, token = [], None
    while True:
        kw = {"familyPrefix": family, "sort": "ASC"}
        if token:
            kw["nextToken"] = token
        r = ecs.list_task_definitions(**kw)
        arns += [a for a in r.get("taskDefinitionArns", []) if f":task-definition/{family}:" in a]
        token = r.get("nextToken")
        if not token:
            return arns


def _seed_task_definitions(aws, td):
    ecs = aws["ecs"]
    first = td.get("first_revision", 1)
    revisions = td["revisions"]
    # History first (copies of the oldest real revision), then the real ones.
    wanted = [revisions[0]] * (first - 1) + list(revisions)
    have = len(_family_arns(ecs, td["family"]))
    for rev in wanted[have:]:
        ecs.register_task_definition(
            family=td["family"], networkMode="awsvpc", requiresCompatibilities=["FARGATE"],
            cpu="256", memory="512",
            executionRoleArn=f"arn:aws:iam::{ACCOUNT}:role/ecsTaskExecutionRole",
            containerDefinitions=resolve(aws, container_definitions(td, rev)),
            **({"taskRoleArn": f"arn:aws:iam::{ACCOUNT}:role/{rev['task_role']}"} if rev.get("task_role") else {}))
    return max(have, len(wanted))


def _seed_target_group(aws, tg, vpc):
    elb = aws["elbv2"]
    hc = tg.get("health_check", {})
    health = dict(
        HealthCheckProtocol="HTTP", HealthCheckPath=hc.get("path", "/health"),
        HealthCheckPort=str(hc.get("port", "traffic-port")),
        HealthCheckIntervalSeconds=hc.get("interval_seconds", 5),
        HealthCheckTimeoutSeconds=hc.get("timeout_seconds", 2),
        HealthyThresholdCount=hc.get("healthy_threshold", 2),
        UnhealthyThresholdCount=hc.get("unhealthy_threshold", 2),
        Matcher={"HttpCode": str(hc.get("matcher", "200"))})
    try:
        found = elb.describe_target_groups(Names=[tg["name"]]).get("TargetGroups", [])
    except ClientError:
        found = []
    if found:
        arn = found[0]["TargetGroupArn"]
        elb.modify_target_group(TargetGroupArn=arn, HealthCheckEnabled=True, **health)
        return arn
    return elb.create_target_group(Name=tg["name"], Protocol="HTTP", Port=tg.get("port", 8080), VpcId=vpc,
                                   TargetType="ip", **health)["TargetGroups"][0]["TargetGroupArn"]


def _seed_load_balancer(aws, lb, tg_arn, subnets, sg):
    elb = aws["elbv2"]
    try:
        found = elb.describe_load_balancers(Names=[lb["name"]]).get("LoadBalancers", [])
    except ClientError:
        found = []
    arn = found[0]["LoadBalancerArn"] if found else elb.create_load_balancer(
        Name=lb["name"], Subnets=subnets, SecurityGroups=[sg], Scheme="internet-facing",
        Type="application")["LoadBalancers"][0]["LoadBalancerArn"]
    if not elb.describe_listeners(LoadBalancerArn=arn).get("Listeners"):
        elb.create_listener(LoadBalancerArn=arn, Protocol="HTTP", Port=lb.get("listener_port", 8081),
                            DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}])
    return arn


def _seed_service(aws, cluster, svc, tg_arn, subnets, sg):
    ecs = aws["ecs"]
    grace = svc.get("health_check_grace_period_seconds", 0)
    existing = [s for s in ecs.describe_services(cluster=cluster, services=[svc["name"]]).get("services", [])
                if s.get("status") == "ACTIVE"]
    if existing:
        ecs.update_service(cluster=cluster, service=svc["name"], taskDefinition=svc["task_definition"],
                           desiredCount=svc["desired_count"], healthCheckGracePeriodSeconds=grace,
                           networkConfiguration={"awsvpcConfiguration": {
                               "subnets": subnets, "securityGroups": [sg], "assignPublicIp": "ENABLED"}})
        return
    ecs.create_service(
        cluster=cluster, serviceName=svc["name"], taskDefinition=svc["task_definition"],
        desiredCount=svc["desired_count"], launchType="FARGATE",
        networkConfiguration={"awsvpcConfiguration": {
            "subnets": subnets, "securityGroups": [sg], "assignPublicIp": "ENABLED"}},
        loadBalancers=[{"targetGroupArn": tg_arn, "containerName": svc.get("container", "app"),
                        "containerPort": svc.get("container_port", 8080)}],
        healthCheckGracePeriodSeconds=grace)


def is_ready(aws, cluster, svc, tg_arn):
    s = aws["ecs"].describe_services(cluster=cluster, services=[svc["name"]])["services"][0]
    targets = aws["elbv2"].describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]
    healthy = [t for t in targets if t["TargetHealth"].get("State") == "healthy"]
    wanted = svc["desired_count"]
    on_seed_td = s.get("taskDefinition", "").endswith("/" + svc["task_definition"])
    ready = (on_seed_td and s.get("runningCount") == wanted
             and len(healthy) == wanted and len(targets) == wanted)
    return ready, f"running {s.get('runningCount')}/{wanted}, healthy targets {len(healthy)}/{wanted}"


def seed(aws, spec: dict, wait: bool = True, timeout: int = 180, sleep=time.sleep) -> dict:
    """Bring the environment to the state described by `spec` (a parsed seed.yaml)."""
    ecs = aws["ecs"]
    cluster = spec["cluster"]
    active = [c for c in ecs.describe_clusters(clusters=[cluster]).get("clusters", [])
              if c.get("status") == "ACTIVE"]
    if not active:
        ecs.create_cluster(clusterName=cluster)
    try:
        aws["iam"].create_role(RoleName="ecsTaskExecutionRole", AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole",
                                                    "Principal": {"Service": "ecs-tasks.amazonaws.com"}}]}))
    except ClientError:
        pass  # already there

    for name, value in (spec.get("ssm_parameters") or {}).items():
        aws["ssm"].put_parameter(Name=name, Value=str(value), Type="SecureString", Overwrite=True)

    _seed_iam(aws, spec)
    revisions = {td["family"]: _seed_task_definitions(aws, td) for td in spec["task_definitions"]}
    vpc, subnets, sg = _network(aws)
    if spec.get("security_group"):
        sg = _seed_security_group(aws, spec["security_group"], vpc)
    for extra in spec.get("extra_services") or []:
        _seed_extra_service(aws, cluster, extra, vpc, subnets, sg, sleep, timeout)
    tg_arn = _seed_target_group(aws, spec["target_group"], vpc)
    _seed_load_balancer(aws, spec["load_balancer"], tg_arn, subnets, sg)
    _seed_service(aws, cluster, spec["service"], tg_arn, subnets, sg)

    summary = {"cluster": cluster, "target_group_arn": tg_arn, "revisions": revisions, "ready": not wait}
    if wait:
        deadline = time.time() + timeout
        detail = ""
        while True:
            ready, detail = is_ready(aws, cluster, spec["service"], tg_arn)
            if ready:
                summary["ready"] = True
                break
            if time.time() >= deadline:
                raise SeedError(f"the starting state never became healthy within {timeout}s ({detail})")
            sleep(3)
    if spec.get("incident"):
        # Some scenarios start broken: the environment is brought up healthy, then
        # something is changed behind the learner's back, as in a real incident.
        from .selftest import run_steps
        run_steps(aws, {"cluster": cluster}, spec["incident"], [])
    return summary
