#!/usr/bin/env python3
"""
chatgpt_promptlib_guard.py -- sandbox write-guard for the VERIDIAN ChatGPT
Prompt Library Workspace (task-20260724-141538-chatgpt-prompt-library-infrastructure,
instruction INS-20260724-141341-5f67).

WHAT IT DOES: every other script this task builds (generate_prompt_coverage_report.py,
detect_prompt_duplicates.py, and any future prompt-generation script once a real
OPENAI_API_KEY exists -- see ai-os/CHATGPT_PROMPTLIB_BLOCKER_2026-07-24.yaml) writes
its output files THROUGH this module's guarded_write()/guarded_write_json(), never
via a bare open(). assert_sandboxed() resolves the real path (os.path.realpath,
so a symlink cannot be used to escape the sandbox) and refuses anything outside
SANDBOX_ROOT. This is the same discipline this session's other registries follow
("registry writes come from scripts not hand-authored prose") applied to a
filesystem write boundary instead of a database write boundary.

No prior-art guard script existed to reuse at the time this was built: the sibling
task (task-20260724-141323-chatgpt-audit-workspace-infrastructure) had not yet
produced scripts/chatgpt_audit_guard.py (its PROGRESS.md was still "Not started").
If that task's guard lands first in a future run, prefer unifying rather than
maintaining two near-identical sandbox guards long-term.

SANDBOX_ROOT's 15 subfolders exist for a reason each (not a generic bucket):
  CSV/          the actual prompt rows (CHATGPT_PROMPT_SCHEMA_2026-07-24.yaml columns)
  Intent/       canonical intent taxonomy, once one is authored
  Keywords/     keyword indexes extracted from prompts
  Variables/    <Entity.Attribute> usage extracted from prompts (cross-links VARIABLE_DICTIONARY)
  Capabilities/ per-capability prompt manifests
  Entities/     per-entity prompt manifests
  Modules/      per-module prompt manifests
  Domains/      per-business-domain prompt manifests
  Routes/       prompt-to-route mappings, once ROUTE_REGISTRY is cross-linked
  Coverage/     latest generate_prompt_coverage_report.py output (overwritten each run)
  Validation/   schema-conformance validation results
  Duplicates/   latest detect_prompt_duplicates.py output
  Statistics/   aggregate counts/summaries derived from CSV/
  Reports/      human-readable summaries (markdown) derived from Coverage/Duplicates/Statistics
  History/      timestamped, never-overwritten copies of every report this sandbox produces

Run:
  python3 scripts/chatgpt_promptlib_guard.py init
  python3 scripts/chatgpt_promptlib_guard.py check --path <path>
  python3 scripts/chatgpt_promptlib_guard.py write --path <path> --content-file <file>
Exit code: 0 on success, 1 if the path is outside the sandbox or the operation failed.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

SANDBOX_ROOT = "/opt/veridian/chatgpt-prompt-library"

SUBFOLDERS = [
    "CSV", "Intent", "Keywords", "Variables", "Capabilities", "Entities",
    "Modules", "Domains", "Routes", "Coverage", "Validation", "Duplicates",
    "Statistics", "Reports", "History",
]


class SandboxViolation(PermissionError):
    pass


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def assert_sandboxed(path):
    """Resolve path (absolute, symlinks followed) and refuse anything that
    does not live under SANDBOX_ROOT. A relative path is treated as relative
    to SANDBOX_ROOT itself (the common case for callers), not to the caller's
    cwd -- this is a whitelist boundary, not a path-joining convenience."""
    if not os.path.isabs(path):
        path = os.path.join(SANDBOX_ROOT, path)
    real_root = os.path.realpath(SANDBOX_ROOT)
    real_path = os.path.realpath(path)
    if real_path != real_root and not real_path.startswith(real_root + os.sep):
        raise SandboxViolation(
            f"refused: '{path}' resolves to '{real_path}', which is outside the "
            f"ChatGPT Prompt Library sandbox ({real_root})"
        )
    return real_path


def init_sandbox():
    """Idempotent: creates SANDBOX_ROOT + all 15 subfolders. This is the only
    sanctioned way to create the sandbox tree -- do not mkdir it by hand."""
    created = []
    real_root = assert_sandboxed(SANDBOX_ROOT)
    os.makedirs(real_root, exist_ok=True)
    for sub in SUBFOLDERS:
        sub_path = os.path.join(real_root, sub)
        assert_sandboxed(sub_path)
        if not os.path.isdir(sub_path):
            os.makedirs(sub_path, exist_ok=True)
            created.append(sub)
    return {"root": real_root, "subfolders": SUBFOLDERS, "newly_created": created}


def guarded_write(path, content, mode="w", encoding="utf-8"):
    """Write text content to `path` after asserting it is inside the sandbox.
    Creates parent directories as needed (still inside the sandbox -- each
    parent is itself checked via assert_sandboxed through os.makedirs' walk
    only being reachable after the leaf path already passed the check)."""
    real_path = assert_sandboxed(path)
    os.makedirs(os.path.dirname(real_path), exist_ok=True)
    with open(real_path, mode, encoding=encoding) as f:
        f.write(content)
    return real_path


def guarded_write_json(path, obj, indent=2):
    return guarded_write(path, json.dumps(obj, indent=indent, default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the sandbox root + all 15 subfolders (idempotent)")

    p_check = sub.add_parser("check", help="check whether a path is inside the sandbox, without writing")
    p_check.add_argument("--path", required=True)

    p_write = sub.add_parser("write", help="write content to a path inside the sandbox")
    p_write.add_argument("--path", required=True)
    group = p_write.add_mutually_exclusive_group(required=True)
    group.add_argument("--content", help="literal content to write")
    group.add_argument("--content-file", dest="content_file", help="read content from this file")

    args = parser.parse_args()

    try:
        if args.cmd == "init":
            result = init_sandbox()
            print(json.dumps({"ok": True, "action": "init", "ts": _now_iso(), **result}, indent=2))
        elif args.cmd == "check":
            real_path = assert_sandboxed(args.path)
            print(json.dumps({"ok": True, "action": "check", "path": args.path, "resolved": real_path}, indent=2))
        elif args.cmd == "write":
            content = args.content if args.content is not None else open(args.content_file, encoding="utf-8").read()
            real_path = guarded_write(args.path, content)
            print(json.dumps({"ok": True, "action": "write", "path": real_path, "bytes": len(content)}, indent=2))
    except SandboxViolation as e:
        print(json.dumps({"ok": False, "error": "sandbox_violation", "detail": str(e)}, indent=2))
        sys.exit(1)
    except OSError as e:
        print(json.dumps({"ok": False, "error": "os_error", "detail": str(e)}, indent=2))
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
