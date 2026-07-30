#!/usr/bin/env python3
"""
chatgpt_audit_versioning.py -- real, unique, incrementing audit-ID +
timestamped filename allocation for the ChatGPT Audit Workspace
(INS-20260724-141101-89d8, task-20260724-141323-chatgpt-audit-workspace-
infrastructure), SCOPE item 4.

Every audit record gets a filename of the form
  <Subfolder>/AUDIT-<seq>-<ts>.yaml
(e.g. Architecture/AUDIT-000001-20260724T141530Z.yaml). The sequence number
is a real, monotonically-incrementing counter persisted at
<ALLOWED_ROOT>/Metadata/.audit_sequence.json, advanced under an flock()
exclusive lock so two concurrent callers can never allocate the same id --
the same discipline scripts/superboss-register.py's _write_lock() uses for
its own writes. allocate() then re-checks the target path does not already
exist on disk (belt-and-suspenders on top of the counter) and loops to the
next sequence number rather than ever returning a path that would overwrite
something -- "never overwrite" is enforced here, not just documented.

Run: python3 scripts/chatgpt_audit_versioning.py allocate --subfolder Architecture
"""
import argparse
import fcntl
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chatgpt_audit_guard import ALLOWED_ROOT, assert_path_allowed  # noqa: E402

SEQUENCE_FILE = os.path.join(ALLOWED_ROOT, "Metadata", ".audit_sequence.json")

VALID_SUBFOLDERS = [
    "Architecture", "Business", "Capabilities", "Modules", "Metadata", "Database",
    "Rules", "Workflow", "APIs", "UI", "Reports", "Security", "Performance", "AI",
    "Prompts", "Routes", "Testing", "Dependencies", "Integrations", "Observability",
    "Release", "Recommendations", "History",
]


def _next_sequence():
    os.makedirs(os.path.dirname(SEQUENCE_FILE), exist_ok=True)
    # 'a+' so a first-ever run creates the file; explicit flock() around the
    # full read-modify-write so two concurrent processes never see + bump
    # the same last_id.
    with open(SEQUENCE_FILE, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            raw = f.read().strip()
            state = json.loads(raw) if raw else {"last_id": 0}
            next_id = state["last_id"] + 1
            state["last_id"] = next_id
            f.seek(0)
            f.truncate()
            f.write(json.dumps(state))
            f.flush()
            os.fsync(f.fileno())
            return next_id
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def allocate(subfolder, extension="yaml"):
    if subfolder not in VALID_SUBFOLDERS:
        raise ValueError(f"unknown subfolder '{subfolder}', must be one of {VALID_SUBFOLDERS}")
    while True:
        seq = _next_sequence()
        audit_id = f"AUDIT-{seq:06d}"
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{audit_id}-{ts}.{extension}"
        path = os.path.join(ALLOWED_ROOT, subfolder, filename)
        assert_path_allowed(path)  # same mechanical guard every audit write goes through
        if not os.path.exists(path):
            return {"audit_id": audit_id, "timestamp": ts, "subfolder": subfolder, "path": path}
        # counter collided with a file already on disk (e.g. a manually
        # placed copy) -- loop for the next sequence number, never overwrite.


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_alloc = sub.add_parser("allocate")
    p_alloc.add_argument("--subfolder", required=True, choices=VALID_SUBFOLDERS)
    p_alloc.add_argument("--extension", default="yaml")
    args = parser.parse_args()
    if args.cmd == "allocate":
        result = allocate(args.subfolder, args.extension)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
