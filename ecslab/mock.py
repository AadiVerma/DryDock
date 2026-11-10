"""Mock mode: an in-memory stand-in for the handful of AWS calls ECS Lab makes.

It exists so the seeder, assertions and runner can be unit tested without
Docker. It is NOT an emulator and models exactly one behaviour: a container
whose image is ecslab/checkout-api:1.5.x crashes on startup unless
PAYMENTS_API_URL is set, the way the real task image does.

Enable for the CLI with ECSLAB_MOCK=1.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from botocore.exceptions import ClientError

ACCOUNT, REGION = "000000000000", "us-east-1"


def _err(code, msg, op):
    return ClientError({"Error": {"Code": code, "Message": msg}}, op)


class FakeAWS:
    def __init__(self, persist: Path | None = None):
        self.persist = persist
        self.s = {"clusters": {}, "tds": {}, "services": {}, "tgs": {}, "lbs": {}, "standalone": [], "stopped": [],
                  "sgs": {"default": {"GroupId": "sg-default", "GroupName": "default", "IpPermissions": []}},
                  "policies": {}, "secrets": {}, "namespaces": {}, "sd": {}}
        if persist and persist.exists():
            self.s.update(json.loads(persist.read_text()))

    def client(self, name):
        return _Client(self, name)

    def _save(self):
        if self.persist:
            self.persist.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so a concurrent reader never sees a half-written file.
            tmp = self.persist.with_name(f"{self.persist.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(self.s))
            os.replace(tmp, self.persist)

    # ---- model ---------------------------------------------------------- #
    def _td_arn(self, family, rev):
        return f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/{family}:{rev}"

    def _resolve_td(self, ref):
        name = ref.rsplit("/", 1)[-1]
        family, _, rev = name.partition(":")
        revs = self.s["tds"].get(family, [])
        rev = int(rev) if rev else len(revs)
        if not 1 <= rev <= len(revs):
            raise _err("ClientException", f"Unable to describe task definition {ref}", "DescribeTaskDefinition")
        return family, rev, revs[rev - 1]

    def _crashes(self, td_arn):
        _, _, containers = self._resolve_td(td_arn)
        c = containers[0]
        env = {e["name"]: e["value"] for e in c.get("environment", [])}
        return ":1.5" in c["image"] and not env.get("PAYMENTS_API_URL")

    def _tasks(self, svc):
        cluster, name, td = svc["cluster"], svc["serviceName"], svc["taskDefinition"]
        rev = td.rsplit(":", 1)[-1]
        gen = svc.get("gen", 0)   # bumped when a task is stopped, so the service "replaces" it
        base = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/{cluster}/{name}-{rev}-" + (f"g{gen}-" if gen else "")
        if self._crashes(td):
            return [{"taskArn": f"{base}dead{i}", "taskDefinitionArn": td, "lastStatus": "STOPPED",
                     "desiredStatus": "STOPPED", "stopCode": "EssentialContainerExited",
                     "stoppedReason": "Essential container in task exited",
                     "containers": [{"name": "app", "exitCode": 1, "lastStatus": "STOPPED"}]} for i in range(3)]
        return [{"taskArn": f"{base}{i}", "taskDefinitionArn": td, "lastStatus": "RUNNING",
                 "desiredStatus": "RUNNING", "containers": [{"name": "app", "lastStatus": "RUNNING"}]}
                for i in range(svc["desiredCount"])]

    def _describe_service(self, svc):
        running = [t for t in self._tasks(svc) if t["lastStatus"] == "RUNNING"]
        return {**svc, "status": "ACTIVE", "runningCount": len(running), "pendingCount": 0,
                "deployments": [{"status": "PRIMARY", "rolloutState": "COMPLETED",
                                 "taskDefinition": svc["taskDefinition"]}],
                "events": [{"message": f"(service {svc['serviceName']}) has reached a steady state."}]}

    # ---- ecs ------------------------------------------------------------ #
    def ecs_describe_clusters(self, clusters=()):
        names = [c.rsplit("/", 1)[-1] for c in clusters]
        return {"clusters": [self.s["clusters"][c] for c in names if c in self.s["clusters"]]}

    def ecs_create_cluster(self, clusterName):
        self.s["clusters"][clusterName] = {"clusterName": clusterName, "status": "ACTIVE"}
        return {"cluster": self.s["clusters"][clusterName]}

    def ecs_list_task_definitions(self, familyPrefix="", **_):
        return {"taskDefinitionArns": [self._td_arn(f, i + 1) for f, revs in sorted(self.s["tds"].items())
                                       if f.startswith(familyPrefix) for i in range(len(revs))]}

    def ecs_register_task_definition(self, family, containerDefinitions, **_):
        revs = self.s["tds"].setdefault(family, [])
        revs.append(containerDefinitions)
        return {"taskDefinition": {"taskDefinitionArn": self._td_arn(family, len(revs)), "family": family,
                                   "revision": len(revs), "containerDefinitions": containerDefinitions}}

    def ecs_describe_task_definition(self, taskDefinition):
        family, rev, containers = self._resolve_td(taskDefinition)
        return {"taskDefinition": {"taskDefinitionArn": self._td_arn(family, rev), "family": family,
                                   "revision": rev,
                                   "containerDefinitions": json.loads(json.dumps(containers))}}

    def ecs_describe_services(self, cluster, services):
        out = [self._describe_service(self.s["services"][n.rsplit("/", 1)[-1]]) for n in services
               if n.rsplit("/", 1)[-1] in self.s["services"]]
        return {"services": out, "failures": []}

    def ecs_create_service(self, cluster, serviceName, taskDefinition, desiredCount, **kw):
        family, rev, _ = self._resolve_td(taskDefinition)
        self.s["services"][serviceName] = {
            "cluster": cluster, "serviceName": serviceName, "taskDefinition": self._td_arn(family, rev),
            "desiredCount": desiredCount, "loadBalancers": kw.get("loadBalancers", []),
            "networkConfiguration": kw.get("networkConfiguration"),
            "healthCheckGracePeriodSeconds": kw.get("healthCheckGracePeriodSeconds", 0)}
        return {"service": self._describe_service(self.s["services"][serviceName])}

    def ecs_update_service(self, cluster, service, **kw):
        svc = self.s["services"][service.rsplit("/", 1)[-1]]
        if "taskDefinition" in kw:
            family, rev, _ = self._resolve_td(kw["taskDefinition"])
            svc["taskDefinition"] = self._td_arn(family, rev)
        for k in ("desiredCount", "healthCheckGracePeriodSeconds", "networkConfiguration"):
            if k in kw:
                svc[k] = kw[k]
        return {"service": self._describe_service(svc)}

    def ecs_delete_service(self, cluster, service, **_):
        self.s["services"].pop(service.rsplit("/", 1)[-1], None)
        return {}

    def _all_tasks(self, serviceName=None):
        tasks = [t for n, svc in self.s["services"].items() if serviceName in (None, n) for t in self._tasks(svc)]
        if serviceName is None:
            tasks += self.s["standalone"]
        return tasks + [t for t in self.s["stopped"] if serviceName in (None, t.get("service"))]

    def ecs_list_tasks(self, cluster, serviceName=None, desiredStatus="RUNNING", **_):
        return {"taskArns": [t["taskArn"] for t in self._all_tasks(serviceName) if t["desiredStatus"] == desiredStatus]}

    def ecs_stop_task(self, cluster, task, reason="Task stopped by user", **_):
        tid = task.rsplit("/", 1)[-1]
        found = next((t for t in self._all_tasks() if t["taskArn"].endswith("/" + tid)), None)
        if not found or found["lastStatus"] == "STOPPED":
            raise _err("InvalidParameterException", "The referenced task was not found.", "StopTask")
        stopped = {**found, "lastStatus": "STOPPED", "desiredStatus": "STOPPED", "stopCode": "UserInitiated",
                   "stoppedReason": reason, "containers": [{"name": "app", "exitCode": 137, "lastStatus": "STOPPED"}]}
        if found in self.s["standalone"]:
            self.s["standalone"].remove(found)
        else:   # a service task: the scheduler replaces it
            svc = next(v for v in self.s["services"].values() if any(t is found or t["taskArn"] == found["taskArn"]
                                                                     for t in self._tasks(v)))
            stopped["service"] = svc["serviceName"]
            svc["gen"] = svc.get("gen", 0) + 1
        self.s["stopped"].append(stopped)
        return {"task": stopped}

    def ecs_run_task(self, cluster, taskDefinition, count=1, **_):
        family, rev, _c = self._resolve_td(taskDefinition)
        td = self._td_arn(family, rev)
        made = []
        for _i in range(count):
            n = len(self.s["standalone"]) + len(self.s["stopped"]) + 1
            crash = self._crashes(td)
            t = {"taskArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/{cluster}/run-{family}-{rev}-{n}", "taskDefinitionArn": td,
                 "group": f"family:{family}", "lastStatus": "STOPPED" if crash else "RUNNING",
                 "desiredStatus": "STOPPED" if crash else "RUNNING",
                 "containers": [{"name": "app", "lastStatus": "STOPPED" if crash else "RUNNING",
                                 **({"exitCode": 1} if crash else {})}]}
            if crash:
                t.update(stopCode="EssentialContainerExited", stoppedReason="Essential container in task exited")
            (self.s["stopped"] if crash else self.s["standalone"]).append(t)
            made.append(t)
        return {"tasks": made, "failures": []}

    def ecs_list_clusters(self, **_):
        return {"clusterArns": [f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/{c}" for c in self.s["clusters"]]}

    def ecs_list_services(self, cluster, **_):
        return {"serviceArns": [f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/{cluster}/{n}"
                                for n, v in self.s["services"].items() if v["cluster"] == cluster.rsplit("/", 1)[-1]]}

    def ecs_list_task_definition_families(self, **_):
        return {"families": sorted(self.s["tds"])}

    def ecs_describe_tasks(self, cluster, tasks):
        every = {t["taskArn"]: t for t in self._all_tasks()}
        by_id = {arn.rsplit("/", 1)[-1]: t for arn, t in every.items()}   # AWS accepts an ARN or a bare ID
        return {"tasks": [every.get(a) or by_id[a] for a in tasks if a in every or a in by_id]}

    # ---- elbv2 ---------------------------------------------------------- #
    def elbv2_describe_target_groups(self, Names=None):
        if Names is None:
            return {"TargetGroups": list(self.s["tgs"].values())}
        missing = [n for n in Names if n not in self.s["tgs"]]
        if missing:
            raise _err("TargetGroupNotFound", f"Target groups '{missing}' not found", "DescribeTargetGroups")
        return {"TargetGroups": [self.s["tgs"][n] for n in Names]}

    def elbv2_create_target_group(self, Name, **kw):
        arn = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:targetgroup/{Name}/0123456789abcdef"
        self.s["tgs"][Name] = {"TargetGroupName": Name, "TargetGroupArn": arn, "HealthCheckEnabled": True, **kw}
        return {"TargetGroups": [self.s["tgs"][Name]]}

    def elbv2_modify_target_group(self, TargetGroupArn, **kw):
        tg = next(t for t in self.s["tgs"].values() if t["TargetGroupArn"] == TargetGroupArn)
        tg.update(kw)
        return {"TargetGroups": [tg]}

    def elbv2_delete_target_group(self, TargetGroupArn):
        self.s["tgs"] = {n: t for n, t in self.s["tgs"].items() if t["TargetGroupArn"] != TargetGroupArn}
        return {}

    def elbv2_describe_target_health(self, TargetGroupArn):
        out = []
        for svc in self.s["services"].values():
            if any(lb.get("targetGroupArn") == TargetGroupArn for lb in svc.get("loadBalancers", [])):
                for i, t in enumerate(self._tasks(svc)):
                    if t["lastStatus"] == "RUNNING":
                        out.append({"Target": {"Id": f"172.18.0.{i + 3}", "Port": 8080},
                                    "TargetHealth": {"State": "healthy"}})
        return {"TargetHealthDescriptions": out}

    def elbv2_describe_load_balancers(self, Names=()):
        missing = [n for n in Names if n not in self.s["lbs"]]
        if missing:
            raise _err("LoadBalancerNotFound", f"Load balancers '{missing}' not found", "DescribeLoadBalancers")
        return {"LoadBalancers": [self.s["lbs"][n] for n in Names]}

    def elbv2_create_load_balancer(self, Name, **_):
        arn = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/{Name}/0123456789abcdef"
        self.s["lbs"][Name] = {"LoadBalancerName": Name, "LoadBalancerArn": arn, "Listeners": []}
        return {"LoadBalancers": [self.s["lbs"][Name]]}

    def elbv2_describe_listeners(self, LoadBalancerArn):
        lb = next(l for l in self.s["lbs"].values() if l["LoadBalancerArn"] == LoadBalancerArn)
        return {"Listeners": lb["Listeners"]}

    def elbv2_create_listener(self, LoadBalancerArn, **kw):
        lb = next(l for l in self.s["lbs"].values() if l["LoadBalancerArn"] == LoadBalancerArn)
        lb["Listeners"].append({"Port": kw.get("Port")})
        return {"Listeners": lb["Listeners"]}

    # ---- logs ----------------------------------------------------------- #
    def _log_lines(self, task):
        if task.get("stopCode") == "EssentialContainerExited":
            return ["checkout-api 1.5.0 starting", "FATAL: missing required environment variable PAYMENTS_API_URL"]
        return ["checkout-api starting", "checkout-api listening on port 8080"]

    def logs_describe_log_groups(self, **_):
        groups = sorted({c["logConfiguration"]["options"]["awslogs-group"] for revs in self.s["tds"].values()
                         for containers in revs for c in containers if c.get("logConfiguration")})
        return {"logGroups": [{"logGroupName": g, "creationTime": 1794298000000} for g in groups]}

    def logs_describe_log_streams(self, logGroupName, **_):
        tasks = self._all_tasks()
        return {"logStreams": [{"logStreamName": "ecs/app/" + t["taskArn"].rsplit("/", 1)[-1],
                                "lastEventTimestamp": 1794298000000 + i} for i, t in enumerate(tasks)]}

    def logs_filter_log_events(self, logGroupName, **_):
        out = []
        for i, t in enumerate(self._all_tasks()):
            for j, m in enumerate(self._log_lines(t)):
                out.append({"timestamp": 1794298000000 + i * 10 + j, "message": m,
                            "logStreamName": "ecs/app/" + t["taskArn"].rsplit("/", 1)[-1]})
        return {"events": out}

    def logs_get_log_events(self, logGroupName, logStreamName, **_):
        task_id = logStreamName.rsplit("/", 1)[-1]
        for t in self._all_tasks():
            if t["taskArn"].endswith("/" + task_id):
                return {"events": [{"timestamp": 1794298000000 + i, "message": m}
                                   for i, m in enumerate(self._log_lines(t))]}
        raise _err("ResourceNotFoundException", "The specified log stream does not exist.", "GetLogEvents")

    # ---- the rest ------------------------------------------------------- #
    def ec2_describe_vpcs(self, **_):
        return {"Vpcs": [{"VpcId": "vpc-default"}]}

    def ec2_describe_subnets(self, **_):
        return {"Subnets": [{"SubnetId": "subnet-a"}, {"SubnetId": "subnet-b"}]}

    def ec2_describe_security_groups(self, Filters=(), GroupIds=None, **_):
        groups = list(self.s["sgs"].values())
        if GroupIds:
            groups = [g for g in groups if g["GroupId"] in GroupIds]
        for f in Filters or ():
            if f["Name"] == "group-name":
                groups = [g for g in groups if g["GroupName"] in f["Values"]]
        return {"SecurityGroups": groups}

    def ec2_create_security_group(self, GroupName, **_):
        self.s["sgs"][GroupName] = {"GroupId": f"sg-{GroupName}", "GroupName": GroupName, "IpPermissions": []}
        return {"GroupId": f"sg-{GroupName}"}

    def _sg(self, GroupId):
        return next(g for g in self.s["sgs"].values() if g["GroupId"] == GroupId)

    def ec2_authorize_security_group_ingress(self, GroupId, IpPermissions, **_):
        self._sg(GroupId)["IpPermissions"] += IpPermissions
        return {}

    def ec2_revoke_security_group_ingress(self, GroupId, **_):
        self._sg(GroupId)["IpPermissions"] = []
        return {}

    def iam_create_role(self, RoleName=None, AssumeRolePolicyDocument=None, **_):
        self.s.setdefault("roles", {})[RoleName] = AssumeRolePolicyDocument
        return {}

    def iam_list_roles(self, **_):
        return {"Roles": [{"RoleName": n, "Arn": f"arn:aws:iam::{ACCOUNT}:role/{n}"} for n in sorted(self.s.get("roles", {}))]}

    def iam_get_role(self, RoleName):
        if RoleName not in self.s.get("roles", {}):
            raise _err("NoSuchEntity", f"The role with name {RoleName} cannot be found.", "GetRole")
        return {"Role": {"RoleName": RoleName, "Arn": f"arn:aws:iam::{ACCOUNT}:role/{RoleName}",
                         "AssumeRolePolicyDocument": json.loads(self.s["roles"][RoleName] or "{}")}}

    def secretsmanager_list_secrets(self, **_):
        return {"SecretList": [{"Name": n, "ARN": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:{n}-AbCdEf"}
                               for n in sorted(self.s["secrets"])]}

    def secretsmanager_get_secret_value(self, SecretId):
        return {"SecretString": self.s["secrets"][SecretId]}

    def ssm_describe_parameters(self, **_):
        return {"Parameters": [{"Name": n, "Type": "SecureString"} for n in sorted(self.s.get("params", {}))]}

    def ssm_get_parameter(self, Name, WithDecryption=False):
        if Name not in self.s.get("params", {}):
            raise _err("ParameterNotFound", f"Parameter {Name} not found.", "GetParameter")
        return {"Parameter": {"Name": Name, "Type": "SecureString", "Value": self.s["params"][Name] if WithDecryption else "***"}}

    def ssm_put_parameter(self, Name=None, Value=None, **_):
        self.s.setdefault("params", {})[Name] = Value
        return {}

    def iam_list_role_policies(self, RoleName):
        return {"PolicyNames": sorted(self.s["policies"].get(RoleName, {}))}

    def iam_put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        self.s["policies"].setdefault(RoleName, {})[PolicyName] = PolicyDocument
        return {}

    def iam_delete_role_policy(self, RoleName, PolicyName):
        self.s["policies"].get(RoleName, {}).pop(PolicyName, None)
        return {}

    def iam_get_role_policy(self, RoleName, PolicyName):
        return {"PolicyDocument": json.loads(self.s["policies"][RoleName][PolicyName])}

    def iam_list_attached_role_policies(self, RoleName):
        return {"AttachedPolicies": []}

    def s3_create_bucket(self, Bucket=None, **_):
        self.s.setdefault("buckets", [])
        if Bucket not in self.s["buckets"]:
            self.s["buckets"].append(Bucket)
        return {}

    def s3_list_buckets(self, **_):
        return {"Buckets": [{"Name": b} for b in self.s.get("buckets", [])]}

    def servicediscovery_list_instances(self, ServiceId=None, **_):
        name = ServiceId.replace("srv-", "")
        running = [t for n, svc in self.s["services"].items() if n == name for t in self._tasks(svc) if t["lastStatus"] == "RUNNING"]
        return {"Instances": [{"Id": t["taskArn"].rsplit("/", 1)[-1], "Attributes": {"AWS_INSTANCE_IPV4": f"172.18.0.{i + 9}",
                                                                                  "AWS_INSTANCE_PORT": "8080"}}
                              for i, t in enumerate(running)]}

    def secretsmanager_create_secret(self, Name, SecretString=None, **_):
        self.s["secrets"][Name] = SecretString
        return {"ARN": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:{Name}-AbCdEf"}

    def secretsmanager_put_secret_value(self, SecretId, SecretString=None, **_):
        self.s["secrets"][SecretId] = SecretString
        return {}

    def secretsmanager_describe_secret(self, SecretId):
        if SecretId not in self.s["secrets"]:
            raise _err("ResourceNotFoundException", "Secrets Manager can't find the specified secret.", "DescribeSecret")
        return {"ARN": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:{SecretId}-AbCdEf", "Name": SecretId}

    def servicediscovery_list_namespaces(self, **_):
        return {"Namespaces": [{"Id": i, "Name": n} for n, i in self.s["namespaces"].items()]}

    def servicediscovery_create_private_dns_namespace(self, Name, **_):
        self.s["namespaces"][Name] = f"ns-{len(self.s['namespaces']) + 1}"
        return {"OperationId": Name}

    def servicediscovery_get_operation(self, OperationId):
        return {"Operation": {"Status": "SUCCESS", "Targets": {"NAMESPACE": self.s["namespaces"][OperationId]}}}

    def servicediscovery_list_services(self, **_):
        return {"Services": [{"Name": n, "Arn": a} for n, a in self.s["sd"].items()]}

    def servicediscovery_create_service(self, Name, **_):
        self.s["sd"][Name] = f"arn:aws:servicediscovery:{REGION}:{ACCOUNT}:service/srv-{Name}"
        return {"Service": {"Arn": self.s["sd"][Name]}}


class _Client:
    def __init__(self, fake, service):
        self._fake, self._service = fake, service

    def __getattr__(self, op):
        fn = getattr(self._fake, f"{self._service}_{op}", None)
        if fn is None:
            raise AttributeError(f"mock mode does not implement {self._service}.{op}")

        def call(**kwargs):
            result = fn(**kwargs)
            self._fake._save()
            return result
        return call
