#!/usr/bin/env python3
"""
ingest_chatgpt_audit_response.py -- manual-bridge response ingester for the
ChatGPT Audit Workspace (INS-20260724-214122-f0ed,
task-20260724-214215-chatgpt-manual-bridge-workflow).

Pairs with scripts/generate_chatgpt_audit_request.py. The Owner pastes the
request text (produced by that script) into a free ChatGPT web session, saves
the reply to a file (or pipes it via stdin), and this script:
  1. parses it as YAML,
  2. validates every required field in ai-os/CHATGPT_AUDIT_RECORD_SCHEMA_2026-07-24.yaml
     is present and well-formed (never trusts audit_id/timestamp from the
     response -- those are always allocated fresh here, never hand-typed or
     LLM-typed),
  3. allocates a real audit_id + never-overwritten path via
     scripts/chatgpt_audit_versioning.py,
  4. writes the final record through scripts/chatgpt_audit_guard.py's
     guarded_write (the only sanctioned write path for this workspace),
  5. refreshes the master catalogue + local index via
     scripts/generate_chatgpt_audit_index.py.

No LLM API call happens here -- this is a pure parse/validate/write script
over content the Owner already obtained manually.

Run (domain mode, unchanged):
  python3 scripts/ingest_chatgpt_audit_response.py --domain Security --file /tmp/chatgpt_reply.yaml
  cat /tmp/chatgpt_reply.yaml | python3 scripts/ingest_chatgpt_audit_response.py --domain Security

Run (module mode, task-20260725-063204-module-scoped-zai-gap-audit): files a
module-scoped response produced by generate_chatgpt_audit_request.py --module
into the schema's existing 'Modules' domain subfolder, linked to its real
module/repo via the schema's own modules_analysed + files_analysed fields
(no new linkage fields added):
  python3 scripts/ingest_chatgpt_audit_response.py --module board_evaluations --repo compliance-tracker --file /tmp/zai_reply.yaml

Exit code: 0 on a successful, validated write; 1 if parsing/validation fails
(nothing is written in that case) or the catalogue refresh fails.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from chatgpt_audit_guard import guarded_write  # noqa: E402
from chatgpt_audit_versioning import VALID_SUBFOLDERS, allocate  # noqa: E402

REPO_ROOT = os.path.dirname(SCRIPT_DIR)
SCHEMA_PATH = os.path.join(REPO_ROOT, "ai-os", "CHATGPT_AUDIT_RECORD_SCHEMA_2026-07-24.yaml")
INDEX_SCRIPT = os.path.join(SCRIPT_DIR, "generate_chatgpt_audit_index.py")

# Allocated fresh by this script every time -- never trusted from the
# response body even if ChatGPT included them.
ALLOCATED_FIELDS = {"audit_id", "timestamp"}

COMMIT_HASH_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
VALID_PRIORITIES = {"critical", "high", "medium", "low"}


def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_record(doc, schema, module=None, repo=None):
    """Returns (errors, warnings). errors block the write; warnings are
    surfaced but non-fatal (e.g. a files_analysed path this server can't
    independently confirm). module/repo are only passed in module mode
    (task-20260725-063204) -- when present, this also sanity-checks the
    response actually links back to the real module/repo it was requested
    for, via the schema's existing modules_analysed/files_analysed fields
    (no new linkage fields)."""
    errors = []
    warnings = []

    if not isinstance(doc, dict):
        return [f"parsed YAML is a {type(doc).__name__}, expected a mapping (dict) at the top level"], []

    for field in schema["fields"]:
        name = field["name"]
        if name in ALLOCATED_FIELDS:
            continue
        if not field.get("required"):
            continue
        if name not in doc or doc[name] in (None, "", []):
            errors.append(f"missing required field: {name}")

    if "files_analysed" in doc and not isinstance(doc["files_analysed"], list):
        errors.append("files_analysed must be a list")
    if "modules_analysed" in doc and not isinstance(doc["modules_analysed"], list):
        errors.append("modules_analysed must be a list")
    if "standards_used" in doc and not isinstance(doc["standards_used"], list):
        errors.append("standards_used must be a list")

    commit_hash = doc.get("commit_hash")
    if commit_hash and not COMMIT_HASH_RE.match(str(commit_hash)):
        errors.append(f"commit_hash '{commit_hash}' is not a full 40-character git SHA")

    priority = doc.get("priority")
    if priority and str(priority).lower() not in VALID_PRIORITIES:
        errors.append(f"priority '{priority}' not in {sorted(VALID_PRIORITIES)}")

    for numeric_field in ("risk_score", "confidence_pct"):
        val = doc.get(numeric_field)
        if val is not None:
            try:
                fval = float(val)
            except (TypeError, ValueError):
                errors.append(f"{numeric_field} '{val}' is not a number")
            else:
                if not (0 <= fval <= 100):
                    errors.append(f"{numeric_field} {fval} is outside range 0-100")

    for path in doc.get("files_analysed") or []:
        path_str = str(path)
        if path_str.startswith("repos/"):
            candidate = os.path.join("/opt/veridian", path_str)
        else:
            candidate = os.path.join("/opt/veridian/repos", path_str)
        if not os.path.isfile(candidate):
            warnings.append(f"files_analysed entry not found on disk (not fatal): {path}")

    if module:
        modules_analysed = [str(m).lower().replace("-", "_") for m in (doc.get("modules_analysed") or [])]
        if module.lower().replace("-", "_") not in modules_analysed:
            warnings.append(
                f"modules_analysed {doc.get('modules_analysed')} does not contain the requested "
                f"module '{module}' (not fatal, but check the response actually covers this module)"
            )
    if repo:
        files_analysed = doc.get("files_analysed") or []
        prefix = f"repos/{repo}/"
        mismatched = [p for p in files_analysed if not str(p).startswith(prefix)]
        if mismatched:
            warnings.append(
                f"files_analysed entries not prefixed 'repos/{repo}/' (not fatal, but check repo linkage): "
                f"{mismatched}"
            )

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--domain", choices=VALID_SUBFOLDERS,
                             help="domain-scoped mode (original) -- target chatgpt-audit subfolder this record belongs to")
    mode_group.add_argument("--module", help="module-scoped mode (task-20260725-063204): real module_key/directory name")
    parser.add_argument("--repo", default=None, help="module mode only: repo name under /opt/veridian/repos/")
    parser.add_argument("--file", default=None, help="path to the saved response; omit to read stdin")
    args = parser.parse_args()

    if args.module and not args.repo:
        print(json.dumps({"ok": False, "error": "--module requires --repo"}, indent=2))
        sys.exit(1)

    subfolder = args.domain or "Modules"

    if args.file:
        if not os.path.isfile(args.file):
            print(json.dumps({"ok": False, "error": f"file not found: {args.file}"}, indent=2))
            sys.exit(1)
        with open(args.file, encoding="utf-8") as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()

    # Strip a markdown code fence if the Owner pasted ChatGPT's reply verbatim
    # including ```yaml / ``` wrappers -- a real, common copy-paste artifact,
    # not a schema requirement.
    stripped = raw.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)

    try:
        doc = yaml.safe_load(stripped)
    except yaml.YAMLError as e:
        print(json.dumps({"ok": False, "error": "yaml_parse_failed", "detail": str(e)}, indent=2))
        sys.exit(1)

    schema = load_schema()
    errors, warnings = validate_record(doc, schema, module=args.module, repo=args.repo)
    if errors:
        print(json.dumps({"ok": False, "error": "validation_failed", "errors": errors, "warnings": warnings}, indent=2))
        sys.exit(1)

    allocation = allocate(subfolder)
    # allocation["timestamp"] is the compact form used in the filename
    # (YYYYMMDDTHHMMSSZ) -- the schema requires full ISO 8601 in the record
    # body, so convert rather than reuse the same string in both places.
    compact_ts = datetime.strptime(allocation["timestamp"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    record = dict(doc)
    record["audit_id"] = allocation["audit_id"]
    record["timestamp"] = compact_ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Stable, schema-field-ordered output rather than whatever key order
    # ChatGPT's YAML happened to use.
    ordered = {}
    for field in schema["fields"]:
        if field["name"] in record:
            ordered[field["name"]] = record[field["name"]]
    for lf in schema["linkage_fields"]["fields"]:
        ordered[lf] = record.get(lf)
    for k, v in record.items():
        if k not in ordered:
            ordered[k] = v

    yaml_text = yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100)
    guarded_write(allocation["path"], yaml_text)

    index_proc = subprocess.run(["python3", INDEX_SCRIPT], capture_output=True, text=True)
    if index_proc.returncode != 0:
        print(json.dumps({
            "ok": False,
            "error": "record_written_but_index_refresh_failed",
            "audit_id": allocation["audit_id"],
            "path": allocation["path"],
            "index_stderr": index_proc.stderr[-2000:],
        }, indent=2))
        sys.exit(1)

    print(json.dumps({
        "ok": True,
        "audit_id": allocation["audit_id"],
        "path": allocation["path"],
        "subfolder": subfolder,
        "domain": args.domain,
        "module": args.module,
        "repo": args.repo,
        "warnings": warnings,
        "catalogue_refreshed": True,
    }, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
