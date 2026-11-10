"""Logging proxy: forwards every request unchanged to Floci and records it.

The learner points the AWS CLI here (AWS_ENDPOINT_URL=http://localhost:4567).
Each call becomes one JSON line: timestamp, method, path, service, action,
response status, and a small `detail` the replay uses.

    python -m ecslab.proxy [--port 4567] [--upstream http://localhost:4566] [--log path]
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "content-length"}
_lock = threading.Lock()


def describe_call(method: str, path: str, headers, body: bytes) -> dict:
    """Work out which AWS service and action a request is."""
    service = None
    m = re.search(r"Credential=[^/]+/\d+/[^/]+/([^/]+)/aws4_request", headers.get("Authorization", "") or "")
    if m:
        service = m.group(1)
    action = None
    target = headers.get("X-Amz-Target")
    if target:                                   # JSON protocols: ECS, CloudWatch Logs
        action = target.rsplit(".", 1)[-1]
    else:                                        # Query protocol: ELBv2, EC2, IAM
        form = {}
        ctype = headers.get("Content-Type", "") or ""
        if body and "x-www-form-urlencoded" in ctype:
            form = parse_qs(body.decode("utf-8", "replace"))
        form.update(parse_qs(urlsplit(path).query))
        if "Action" in form:
            action = form["Action"][0]
    return {"service": service, "action": action or f"{method} {urlsplit(path).path}"}


def detail_for(action: str, req_body: bytes, resp_body: bytes) -> str | None:
    """`stopped` when the learner was looking at stopped tasks."""
    try:
        if action == "ListTasks":
            if json.loads(req_body or b"{}").get("desiredStatus") == "STOPPED":
                return "stopped"
        elif action == "DescribeTasks":
            tasks = json.loads(resp_body or b"{}").get("tasks", [])
            if any(t.get("lastStatus") == "STOPPED" for t in tasks):
                return "stopped"
    except (ValueError, AttributeError):
        pass
    return None


def make_handler(upstream: str, log_path: Path):
    up = urlsplit(upstream)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _forward(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            started = time.time()
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
            status, resp_body, resp_headers = 502, b"", []
            try:
                conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=120)
                conn.request(self.command, self.path, body=body or None, headers=headers)
                resp = conn.getresponse()
                status, resp_body, resp_headers = resp.status, resp.read(), resp.getheaders()
                conn.close()
            except OSError as e:
                resp_body = json.dumps({"__type": "ServiceUnavailable",
                                        "message": f"ECS Lab proxy could not reach the emulator at {upstream}: {e}. "
                                                   f"Is it running? Try: ecslab start"}).encode()
                resp_headers = [("Content-Type", "application/x-amz-json-1.1")]

            call = describe_call(self.command, self.path, self.headers, body)
            entry = {"ts": round(started, 3), "method": self.command, "path": self.path, **call,
                     "status": status, "ms": round((time.time() - started) * 1000)}
            detail = detail_for(call["action"], body, resp_body)
            if detail:
                entry["detail"] = detail
            if "ecslab-console" in (self.headers.get("User-Agent") or ""):
                entry["source"] = "console"   # clicked in the web interface, not typed
            with _lock:
                with log_path.open("a") as f:
                    f.write(json.dumps(entry) + "\n")

            self.send_response(status)
            for k, v in resp_headers:
                if k.lower() not in HOP_BY_HOP:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(resp_body)

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_PATCH = do_OPTIONS = _forward

        def log_message(self, *_):
            pass

    return Handler


def serve(port: int, upstream: str, log_path: Path) -> ThreadingHTTPServer:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch()
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(upstream, log_path))


def main():
    from . import config
    ap = argparse.ArgumentParser(description="ECS Lab logging proxy")
    ap.add_argument("--port", type=int, default=config.PROXY_PORT)
    ap.add_argument("--upstream", default=config.FLOCI_URL)
    ap.add_argument("--log", type=Path, default=config.CALL_LOG)
    args = ap.parse_args()
    server = serve(args.port, args.upstream, args.log)
    print(f"ecslab proxy: localhost:{args.port} -> {args.upstream}, logging to {args.log}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
