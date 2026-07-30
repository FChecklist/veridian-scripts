#!/usr/bin/env python3
"""
notification_engine.py -- Phase 4 (document_notification_data) of
20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml (task
task-20260724-133622-phase4-unify-document-pipeline-pdf-gener), closes_engines: [12].

Implements ai-os/NOTIFICATION_ENGINE_CONTRACT_2026-07-24.yaml's
notification_envelope_schema for the owner_email channel -- the "at minimum a
shared schema + cross-call" half of this phase's objective that this task's
repo boundary allows (the in_app channel stays compliance-tracker's own PR,
see that contract's own cross_call_realized note).

send-owner validates the envelope shape (channel/recipient/subject_or_title/
body_or_message/dedupe_key/source_engine) then maps it onto
scripts/notify-owner.py's real --subject/--body/--dedupe-key CLI -- the exact
mapping notification_envelope_schema.fields documents, not a reinvention of
notify-owner.py's own rate-limiting/Resend-call logic.

Run: python3 scripts/notification_engine.py send-owner --subject ... --body ... [--dedupe-key ...] [--source-engine ...]
"""
import argparse
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NOTIFY_OWNER = f"{SCRIPT_DIR}/notify-owner.py"

VALID_CHANNELS = ["owner_email", "in_app"]


def build_envelope(args):
    return {
        "channel": args.channel,
        "recipient": args.recipient or "OWNER_EMAIL",
        "subject_or_title": args.subject,
        "body_or_message": args.body,
        "dedupe_key": args.dedupe_key,
        "source_engine": args.source_engine,
    }


def cmd_send_owner(args):
    envelope = build_envelope(args)

    if envelope["channel"] != "owner_email":
        print(json.dumps({"error": f"send-owner only implements the owner_email channel, got {envelope['channel']!r}"}))
        sys.exit(1)

    if not os.path.isfile(NOTIFY_OWNER):
        print(json.dumps({"error": f"notify-owner.py not found at {NOTIFY_OWNER}"}))
        sys.exit(1)

    subject = envelope["subject_or_title"]
    if envelope["source_engine"]:
        subject = f"[{envelope['source_engine']}] {subject}"

    cmd = ["python3", NOTIFY_OWNER, "--subject", subject, "--body", envelope["body_or_message"]]
    if envelope["dedupe_key"]:
        cmd += ["--dedupe-key", envelope["dedupe_key"]]
    if args.force:
        cmd += ["--force"]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    print(json.dumps({
        "envelope": envelope,
        "notify_owner_exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }, indent=2))
    sys.exit(proc.returncode)


def cmd_validate_envelope(args):
    try:
        envelope = json.loads(args.envelope)
    except json.JSONDecodeError:
        print(json.dumps({"valid": False, "error": "--envelope must be valid JSON"}))
        sys.exit(1)

    required = ["channel", "recipient", "subject_or_title", "body_or_message"]
    missing = [f for f in required if not envelope.get(f)]
    channel_valid = envelope.get("channel") in VALID_CHANNELS

    valid = not missing and channel_valid
    print(json.dumps({
        "valid": valid,
        "missing_fields": missing,
        "channel_valid": channel_valid,
    }, indent=2))
    sys.exit(0 if valid else 1)


def main():
    parser = argparse.ArgumentParser(description="VERIDIAN Notification Engine -- shared envelope + cross-call")
    sub = parser.add_subparsers(dest="command", required=True)

    p_send = sub.add_parser("send-owner", help="send a notification_envelope_schema-shaped notification "
        "through the owner_email channel (delegates to notify-owner.py)")
    p_send.add_argument("--channel", default="owner_email", choices=VALID_CHANNELS)
    p_send.add_argument("--recipient", default=None)
    p_send.add_argument("--subject", required=True)
    p_send.add_argument("--body", required=True)
    p_send.add_argument("--dedupe-key", default=None)
    p_send.add_argument("--source-engine", default=None, help="e.g. document_ocr_paddleocr, automation_rule_engine")
    p_send.add_argument("--force", action="store_true")
    p_send.set_defaults(func=cmd_send_owner)

    p_validate = sub.add_parser("validate-envelope", help="check a JSON envelope against "
        "notification_envelope_schema's required fields")
    p_validate.add_argument("--envelope", required=True, help="JSON-encoded notification_envelope_schema object")
    p_validate.set_defaults(func=cmd_validate_envelope)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
