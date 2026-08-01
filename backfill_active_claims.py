#!/usr/bin/env python3
"""Backfill ai-os/boss/ACTIVE-CLAIMS.yaml's real entries into the coordination
graph (entity/relation tables in superboss-register.py's SQLite DB), so the
graph is useful immediately rather than empty. One-time/idempotent migration
script -- safe to re-run (log-entity/log-relation are both get-or-create /
append-only respectively; re-running never duplicates an entity, and
re-asserting the same relation is harmless for check-conflict's purposes).

Usage: python3 backfill_active_claims.py --claims-file <path to ACTIVE-CLAIMS.yaml>

Extraction is deliberately best-effort, not full NLU: each `active:` entry's
`claim` + `scope_note` free text is scanned for path-shaped tokens (a repo-
relative file path or a directory ending in '/') via regex, each becomes a
file_area entity, and a 'claims' relation is logged from a task entity (keyed
by a task-id-shaped token pulled from session_label, falling back to the full
session_label) into each file_area found. This mirrors the source file's own
protocol intent -- "file/directory-scoped so a session with zero other
context can act on them correctly" -- without requiring a schema change to
ACTIVE-CLAIMS.yaml itself.
"""
import argparse
import re
import subprocess
import sys

import yaml

TASK_ID_RE = re.compile(r"task-\d{8}-\d{6}[\w-]*")
PATH_RE = re.compile(
    r"\b(?:[\w.\-]+/)+[\w.\-]+\.(?:ts|tsx|py|sql|yaml|yml|md|sh|json|mjs|cjs)\b"
    r"|\b(?:src|drizzle|ai-os|scripts|repos)/[\w.\-/]*/"
)


def _extract_task_key(session_label: str) -> str:
    m = TASK_ID_RE.search(session_label)
    if m:
        return m.group(0)
    return session_label.strip()[:120]


def _extract_file_areas(*texts, limit=20) -> list:
    found = []
    seen = set()
    for text in texts:
        if not text:
            continue
        for m in PATH_RE.finditer(text):
            path = m.group(0)
            if path not in seen:
                seen.add(path)
                found.append(path)
            if len(found) >= limit:
                return found
    return found


def _load_yaml_tolerant(raw_text: str):
    """ACTIVE-CLAIMS.yaml is hand-appended by many independent sessions over
    weeks (6600+ lines as of this backfill) and is not always perfectly
    indented -- confirmed one real entry (2026-07-19 "reevaluate 2045 rows"
    claim) whose `- session_label:` starts at column 0 instead of the
    required 2-space indent under `active:`, which breaks strict
    yaml.safe_load with a ParserError. Rather than let one hand-edit mistake
    make the whole backfill fail, re-indent any column-0 `- ` list-item line
    to the required 2 spaces (the only valid structure here is `active:` /
    `recently_completed:` each followed by a 2-space-indented list -- a
    column-0 dash is never semantically a new top-level list in this file)
    and retry once."""
    try:
        return yaml.safe_load(raw_text)
    except yaml.YAMLError:
        lines = raw_text.splitlines()
        repaired = []
        in_broken_block = False
        for line in lines:
            if not in_broken_block and re.match(r"^-\s", line):
                in_broken_block = True
            elif in_broken_block and (re.match(r"^  - ", line) or re.match(r"^\w[\w-]*:", line)):
                in_broken_block = False  # a properly-indented sibling entry, or a new top-level key -- block over
            if in_broken_block and line.strip():
                repaired.append("  " + line)
            else:
                repaired.append(line)
        return yaml.safe_load("\n".join(repaired))


def _run_gateway(script_path, *cli_args):
    result = subprocess.run(
        [sys.executable, script_path, *cli_args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"WARNING: {cli_args} failed: {result.stderr.strip()}", file=sys.stderr)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims-file", required=True)
    ap.add_argument("--script-path", default=None,
                     help="path to superboss-register.py (default: alongside this script)")
    args = ap.parse_args()

    script_path = args.script_path or (__file__.rsplit("/", 1)[0] + "/superboss-register.py")

    with open(args.claims_file, encoding="utf-8") as f:
        raw_text = f.read()
    doc = _load_yaml_tolerant(raw_text)

    entries = list(doc.get("active", []) or [])
    entries += list(doc.get("recently_completed", []) or [])

    tasks_logged = 0
    file_areas_logged = 0
    relations_logged = 0
    seen_task_keys = {}

    for entry in entries:
        session_label = entry.get("session_label", "")
        if not session_label:
            continue
        task_key = _extract_task_key(session_label)
        # Not every real entry's session_label is task-id-shaped (many are a
        # generic "Super Boss (Claude Desktop) -- this session" repeated
        # across genuinely distinct claims) -- without disambiguation those
        # would silently collapse into one entity, losing real distinct
        # claims. Append a stable per-duplicate suffix instead.
        if task_key in seen_task_keys:
            seen_task_keys[task_key] += 1
            task_key = f"{task_key} [#{seen_task_keys[task_key]}]"
        else:
            seen_task_keys[task_key] = 1

        r = _run_gateway(
            script_path, "log-entity", "--type", "task", "--key", task_key,
            "--metadata", '{"status":"open","source":"ACTIVE-CLAIMS.yaml backfill"}',
        )
        if r.returncode == 0:
            tasks_logged += 1

        areas = _extract_file_areas(entry.get("claim", ""), entry.get("scope_note", ""))
        for area in areas:
            r = _run_gateway(
                script_path, "log-relation",
                "--src-type", "task", "--src-key", task_key,
                "--dst-type", "file_area", "--dst-key", area,
                "--type", "claims", "--created-by", "software",
            )
            if r.returncode == 0:
                relations_logged += 1
                file_areas_logged += 1

    print(f"Backfill complete: {tasks_logged} task entities logged, "
          f"{relations_logged} claims relations logged over "
          f"{file_areas_logged} file_area references, from {len(entries)} "
          f"ACTIVE-CLAIMS.yaml entries.")


if __name__ == "__main__":
    main()
