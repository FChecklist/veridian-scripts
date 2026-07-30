#!/usr/bin/env python3
"""
webhook_receiver.py -- standalone inbound webhook receiver (closes engine
10, Integration Engine, of ai-os/20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml's
phase_3_workflow_automation_integration).

Per that phase's own objective text: "evaluate a standalone OSS
webhook-receiver pattern ... do not install a full iPaaS like n8n without
Owner confirmation". See ai-os/INBOUND_WEBHOOK_RECEIVER_EVALUATION_2026-07-24.yaml
for the full evaluation this file's design follows. Deliberately zero new
pip dependency (Python stdlib http.server only) -- consistent with
AUDITOR_ENGINE_PHASE_PLAN_2026-07-24.yaml's own confirmed finding of zero
iPaaS/workflow-engine dependencies in any of the 3 repos, and this task's
own CONSTRAINTS not to install a full iPaaS without Owner confirmation.

Signature convention deliberately mirrors compliance-tracker's own
outbound webhook-deliver.ts (X-Veridian-Signature: sha256=<hmac>, same
crypto.createHmac("sha256", secret) shape) rather than inventing a new one
-- one signature convention for both directions of this platform's webhook
traffic.

On a verified POST /webhook/<event_type>, calls
scripts/automation_rule_engine.py's evaluate-rules for trigger_type =
"webhook.<event_type>" with the request body as payload -- the first live
implementation of dependency_table's "Integration Engine (10) ->
Automation Engine (9)" edge (previously status: planned in
20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml).

NOT started by any cron entry or systemd unit as part of this task -- see
systemd/veridian-webhook-receiver.service (present, not enabled) and the
matching OWNER_DECISIONS_NEEDED_2026-07-23.yaml entry: a new inbound
network listener is a real infra/security decision (what binds it, what
network path can reach it) beyond the Owner's existing cron-only blanket
approval, so it is not silently made live.

Run standalone for real, local testing:
    VERIDIAN_WEBHOOK_SECRET=<secret> python3 scripts/webhook_receiver.py --port 8099 --bind 127.0.0.1
"""
import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERIDIAN_ROOT = "/opt/veridian"
SCRIPTS = f"{VERIDIAN_ROOT}/scripts"
AUTOMATION_RULE_ENGINE = f"{SCRIPTS}/automation_rule_engine.py"

SIGNATURE_HEADER = "X-Veridian-Signature"
EVENT_PATH_PREFIX = "/webhook/"


def _verify_signature(secret, body_bytes, signature_header):
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    provided = signature_header[len("sha256="):]
    expected = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


class WebhookHandler(BaseHTTPRequestHandler):
    secret = None  # set by main() before serve_forever()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _respond(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.path.startswith(EVENT_PATH_PREFIX):
            self._respond(404, {"error": f"path must start with {EVENT_PATH_PREFIX}"})
            return
        event_type = self.path[len(EVENT_PATH_PREFIX):].split("?")[0].strip("/")
        if not event_type:
            self._respond(400, {"error": "event_type missing from path"})
            return

        content_length = int(self.headers.get("Content-Length", 0) or 0)
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b""

        if self.secret:
            signature_header = self.headers.get(SIGNATURE_HEADER)
            if not _verify_signature(self.secret, body_bytes, signature_header):
                self._respond(401, {"error": "invalid or missing signature"})
                return

        try:
            payload = json.loads(body_bytes) if body_bytes else {}
        except json.JSONDecodeError:
            self._respond(400, {"error": "body is not valid JSON"})
            return

        trigger_type = f"webhook.{event_type}"
        proc = subprocess.run(
            ["python3", AUTOMATION_RULE_ENGINE, "evaluate-rules",
             "--trigger-type", trigger_type, "--payload", json.dumps(payload)],
            capture_output=True, text=True,
        )
        try:
            evaluation = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            evaluation = {"raw_stdout": proc.stdout, "raw_stderr": proc.stderr}

        self._respond(200, {"received": True, "trigger_type": trigger_type, "evaluation": evaluation})

    def do_GET(self):
        if self.path == "/healthz":
            self._respond(200, {"ok": True})
            return
        self._respond(404, {"error": "not found"})


def build_parser():
    p = argparse.ArgumentParser(prog="webhook_receiver.py")
    p.add_argument("--port", type=int, default=8099)
    p.add_argument("--bind", default="127.0.0.1",
                    help="bind address -- defaults to loopback-only; binding 0.0.0.0 is a real network-"
                         "exposure decision, see OWNER_DECISIONS_NEEDED_2026-07-23.yaml")
    return p


def main():
    args = build_parser().parse_args()
    secret = os.environ.get("VERIDIAN_WEBHOOK_SECRET")
    if not secret:
        sys.stderr.write(
            "WARNING: VERIDIAN_WEBHOOK_SECRET not set -- signature verification is DISABLED, every request "
            "will be accepted unauthenticated. Set VERIDIAN_WEBHOOK_SECRET before any real (non-loopback-"
            "testing) use.\n"
        )
    WebhookHandler.secret = secret
    server = ThreadingHTTPServer((args.bind, args.port), WebhookHandler)
    sys.stderr.write(f"webhook_receiver.py listening on {args.bind}:{args.port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
