"""Tiny HTTP server for ECS Lab scenarios.

Behaviour is driven entirely by environment variables, so two task definition
revisions can differ only in configuration:

  APP_VERSION       baked into the image at build time. From 1.5.0 the service
                    talks to the payments API and refuses to start without
                    PAYMENTS_API_URL.
  PORT              port to listen on (default 8080)
  EXIT_IMMEDIATELY  if "1", log a fatal error and exit 1 before listening

Knobs for building other failure modes (all optional):

  REQUIRE_ENV          comma-separated variable names; exit 1 if any is unset
  REQUIRE_HTTP         URL that must answer at startup; exit 1 if it does not
  WRITE_PATH           file the app must be able to write at startup; exit 1 if it cannot
  CATALOG_CACHE_MB     load this much data into memory at startup (alias MEMORY_HOG_MB)
  CACHE_PATH           same as WRITE_PATH
  STARTUP_DELAY_SECONDS  wait this long before listening (slow start)
  EXIT_AFTER_SECONDS   run normally, then exit with EXIT_CODE (default 1)
  HEALTH_PATH          path that answers the health check (default /health)
  HEALTH_STATUS        status code the health check returns (default 200)
  IGNORE_SIGTERM       if "1", ignore SIGTERM so a stop has to kill the container
  INVENTORY_URL        same as REQUIRE_HTTP
  REPORT_SCHEDULE      cron schedule for the report worker; an invalid one crashes the
                       app 15 seconds after it starts (exit 3)
  RECEIPTS_BUCKET      S3 bucket the app must be able to list at startup, using the
                       task role's credentials (needs AWS_ENDPOINT_URL); exit 1 if denied
"""
import datetime
import hashlib
import hmac
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
VERSION = os.environ.get("APP_VERSION", "1.4.2")
REQUIRED = ["PAYMENTS_API_URL"] if tuple(int(x) for x in VERSION.split(".")[:2]) >= (1, 5) else []
REQUIRED += [n.strip() for n in os.environ.get("REQUIRE_ENV", "").split(",") if n.strip()]
# Names a learner sees in a task definition; the older names are kept for the probes.
for new, old in (("CATALOG_CACHE_MB", "MEMORY_HOG_MB"), ("CACHE_PATH", "WRITE_PATH"), ("INVENTORY_URL", "REQUIRE_HTTP")):
    if os.environ.get(new):
        os.environ[old] = os.environ[new]
HEALTH_PATH = os.environ.get("HEALTH_PATH", "/health")
HEALTH_STATUS = int(os.environ.get("HEALTH_STATUS", "200"))


def task_credentials():
    """Credentials the way an AWS SDK finds them in a task: the role endpoint first."""
    rel = os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
    if rel:
        with urllib.request.urlopen("http://169.254.170.2" + rel, timeout=3) as r:
            c = json.load(r)
        return c["AccessKeyId"], c["SecretAccessKey"], c.get("Token"), c.get("RoleArn", "task role")
    # Like an AWS SDK on Fargate: with no task role there are no credentials to find.
    raise RuntimeError("Unable to locate credentials (the task has no usable task role)")


def s3_list(bucket):
    """Signed ListObjectsV2 against AWS_ENDPOINT_URL. Returns the HTTP status and body."""
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "http://ecslab-floci:4566").rstrip("/")
    region = os.environ.get("AWS_REGION", "us-east-1")
    key, secret, token, who = task_credentials()
    host = endpoint.split("://", 1)[1]
    now = datetime.datetime.now(datetime.timezone.utc)
    amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    empty = hashlib.sha256(b"").hexdigest()
    headers = {"host": host, "x-amz-content-sha256": empty, "x-amz-date": amz}
    if token:
        headers["x-amz-security-token"] = token
    signed = ";".join(sorted(headers))
    query = "list-type=2&max-keys=1"
    canonical = "\n".join(["GET", f"/{bucket}", query, "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed, empty])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = ("AWS4" + secret).encode()
    for part in (day, region, "s3", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = f"AWS4-HMAC-SHA256 Credential={key}/{scope}, SignedHeaders={signed}, Signature={sig}"
    req = urllib.request.Request(f"{endpoint}/{bucket}?{query}", headers={k: v for k, v in headers.items() if k != "host"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace"), who
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), who


def fatal(message, code=1):
    print(f"FATAL: {message}", flush=True)
    sys.exit(code)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = 200
        if self.path == HEALTH_PATH:
            status = HEALTH_STATUS
            body = b'{"status":"ok"}' if status == 200 else b'{"status":"degraded"}'
        elif self.path == "/":
            body = ('{"service":"checkout-api","version":"%s"}' % VERSION).encode()
        else:
            status, body = 404, b'{"error":"not found"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("%s %s" % (self.address_string(), fmt % args), flush=True)


def main():
    print(f"checkout-api {VERSION} starting", flush=True)
    for name in REQUIRED:
        if not os.environ.get(name):
            fatal(f"missing required environment variable {name}")
        print(f"config: {name} is set", flush=True)
    if os.environ.get("EXIT_IMMEDIATELY") == "1":
        fatal("could not load configuration, exiting")
    if os.environ.get("IGNORE_SIGTERM") == "1":
        signal.signal(signal.SIGTERM, lambda *_: print("received SIGTERM, still finishing work", flush=True))
    if os.environ.get("WRITE_PATH"):
        try:
            with open(os.environ["WRITE_PATH"], "w") as f:
                f.write("ok")
            print(f"cache: wrote {os.environ['WRITE_PATH']}", flush=True)
        except OSError as e:
            fatal(f"cannot write {os.environ['WRITE_PATH']}: {e.strerror}")
    if os.environ.get("REQUIRE_HTTP"):
        url = os.environ["REQUIRE_HTTP"]
        try:
            urllib.request.urlopen(url, timeout=3).close()
            print(f"dependency: {url} is reachable", flush=True)
        except Exception as e:
            fatal(f"cannot reach dependency {url}: {e}")
    if os.environ.get("RECEIPTS_BUCKET"):
        bucket = os.environ["RECEIPTS_BUCKET"]
        try:
            status, body, who = s3_list(bucket)
        except Exception as e:
            fatal(f"cannot access s3://{bucket}: {e}")
        if status != 200:
            code = body.split("<Code>")[1].split("</Code>")[0] if "<Code>" in body else str(status)
            fatal(f"cannot list s3://{bucket} as {who}: {code} (HTTP {status}) calling s3:ListBucket")
        print(f"storage: s3://{bucket} is accessible as {who}", flush=True)
    if os.environ.get("MEMORY_HOG_MB"):
        mb = int(os.environ["MEMORY_HOG_MB"])
        print(f"cache: loading {mb} MB into memory", flush=True)
        hog = [bytearray(1024 * 1024) for _ in range(mb)]
        for block in hog:                       # touch every page so it is really used
            block[::4096] = b"x" * len(block[::4096])
        print("cache: loaded", flush=True)
    if os.environ.get("STARTUP_DELAY_SECONDS"):
        delay = float(os.environ["STARTUP_DELAY_SECONDS"])
        print(f"warming up for {delay:.0f}s before accepting traffic", flush=True)
        time.sleep(delay)
    schedule = os.environ.get("REPORT_SCHEDULE")
    if schedule is not None:
        if len(schedule.split()) == 5:
            print(f"report worker: scheduled '{schedule}'", flush=True)
        else:   # the worker only parses its schedule when it first wakes up
            def bad_schedule():
                print(f"ERROR: report worker: invalid schedule '{schedule}' (expected 5 cron fields), exiting", flush=True)
                os._exit(3)
            threading.Timer(15, bad_schedule).start()
    if os.environ.get("EXIT_AFTER_SECONDS"):
        after, code = float(os.environ["EXIT_AFTER_SECONDS"]), int(os.environ.get("EXIT_CODE", "1"))

        def die():
            print(f"ERROR: unrecoverable error in worker, exiting with code {code}", flush=True)
            os._exit(code)
        threading.Timer(after, die).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"checkout-api listening on port {PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
