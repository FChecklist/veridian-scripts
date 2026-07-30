#!/usr/bin/env python3
"""
generate_chatgpt_audit_index.py -- auto-index generator for the ChatGPT
Audit Workspace (INS-20260724-141101-89d8, task-20260724-141323-chatgpt-
audit-workspace-infrastructure), SCOPE item 4.

Owner directive: "all registry/catalog data must be produced by scripts, not
hand-authored AI prose." This script is that mechanism for the ChatGPT audit
catalogue -- it never invents a record; it scans the real, live workspace
(ALLOWED_ROOT, the same directory scripts/chatgpt_audit_guard.py confines
writes to) for AUDIT-*.yaml files, best-effort parses each against
ai-os/CHATGPT_AUDIT_RECORD_SCHEMA_2026-07-24.yaml's field list, and writes:

  1. a local searchable index at <ALLOWED_ROOT>/Metadata/INDEX.yaml
  2. the master catalogue at <repo_root>/ai-os/CHATGPT_AUDIT_MASTER_CATALOGUE_2026-07-24.yaml

Both are safe to re-run any time (idempotent -- purely a re-scan, never
mutates an audit record itself). Zero records is an expected, correct result
until the LLM-execution blocker (ai-os/CHATGPT_AUDIT_BLOCKER_2026-07-24.yaml)
clears -- this script does not fabricate placeholder records to make the
catalogue look populated.

Run: python3 scripts/generate_chatgpt_audit_index.py
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chatgpt_audit_guard import ALLOWED_ROOT  # noqa: E402

import yaml  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGUE_PATH = os.path.join(REPO_ROOT, "ai-os", "CHATGPT_AUDIT_MASTER_CATALOGUE_2026-07-24.yaml")
LOCAL_INDEX_PATH = os.path.join(ALLOWED_ROOT, "Metadata", "INDEX.yaml")
SCHEMA_PATH = os.path.join(REPO_ROOT, "ai-os", "CHATGPT_AUDIT_RECORD_SCHEMA_2026-07-24.yaml")

SUMMARY_FIELDS = [
    "audit_id", "timestamp", "repository_version", "commit_hash", "risk_score",
    "priority", "confidence_pct",
]


def _load_subfolders():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = yaml.safe_load(f)
    return schema["domain_subfolders"]


def scan():
    subfolders = _load_subfolders()
    records = []
    parse_errors = []
    by_subfolder = {sf: 0 for sf in subfolders}

    for subfolder in subfolders:
        pattern = os.path.join(ALLOWED_ROOT, subfolder, "AUDIT-*.yaml")
        for path in sorted(glob.glob(pattern)):
            rel_path = os.path.relpath(path, ALLOWED_ROOT)
            try:
                with open(path, encoding="utf-8") as f:
                    doc = yaml.safe_load(f) or {}
            except Exception as e:  # noqa: BLE001 -- a malformed record must surface, not crash the scan
                parse_errors.append({"path": rel_path, "error": str(e)})
                continue
            summary = {"path": rel_path, "subfolder": subfolder}
            for field in SUMMARY_FIELDS:
                summary[field] = doc.get(field)
            records.append(summary)
            by_subfolder[subfolder] += 1

    return records, parse_errors, by_subfolder, subfolders


def build_catalogue(records, parse_errors, by_subfolder, subfolders):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = len(records)
    doc = {
        "meta": {
            "id": "CHATGPT-AUDIT-MASTER-CATALOGUE",
            "generated_ts": now,
            "generated_by": "scripts/generate_chatgpt_audit_index.py (re-run this to refresh -- do not hand-edit)",
            "purpose": (
                "Real, machine-scanned index of every audit record file under "
                f"{ALLOWED_ROOT} -- built for INS-20260724-141101-89d8 "
                "(task-20260724-141323-chatgpt-audit-workspace-infrastructure)."
            ),
            "workspace_root": ALLOWED_ROOT,
            "schema_ref": "ai-os/CHATGPT_AUDIT_RECORD_SCHEMA_2026-07-24.yaml",
            "guard_ref": "scripts/chatgpt_audit_guard.py",
            "local_index_ref": os.path.relpath(LOCAL_INDEX_PATH, "/"),
        },
        "summary": {
            "total_audit_records": total,
            "subfolders_scanned": len(subfolders),
            "by_subfolder": by_subfolder,
            "parse_errors": len(parse_errors),
            "note": (
                "0 records is expected and correct today -- the LLM-execution step is "
                "explicitly out of scope for this task (no OPENAI_API_KEY exists yet, "
                "see ai-os/CHATGPT_AUDIT_BLOCKER_2026-07-24.yaml). This catalogue "
                "populates automatically (re-run this script) once that blocker "
                "clears and real audits are written through scripts/chatgpt_audit_guard.py."
            ) if total == 0 else (
                f"{total} real audit record(s) scanned from disk -- re-run this script "
                "any time to refresh."
            ),
        },
        "records": records,
        "parse_errors": parse_errors,
    }
    return doc


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalogue-out", default=CATALOGUE_PATH)
    parser.add_argument("--local-index-out", default=LOCAL_INDEX_PATH)
    args = parser.parse_args()

    records, parse_errors, by_subfolder, subfolders = scan()
    catalogue = build_catalogue(records, parse_errors, by_subfolder, subfolders)

    os.makedirs(os.path.dirname(args.catalogue_out), exist_ok=True)
    with open(args.catalogue_out, "w", encoding="utf-8") as f:
        yaml.safe_dump(catalogue, f, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100)

    # Local index lives inside ALLOWED_ROOT itself -- write it through the
    # same guard the rest of the workspace's writes go through.
    from chatgpt_audit_guard import guarded_write  # noqa: E402
    guarded_write(args.local_index_out, yaml.safe_dump(catalogue, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100))

    print(json.dumps({
        "catalogue_written": args.catalogue_out,
        "local_index_written": args.local_index_out,
        "total_audit_records": len(records),
        "parse_errors": len(parse_errors),
    }, indent=2))


if __name__ == "__main__":
    main()
