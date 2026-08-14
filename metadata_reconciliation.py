#!/usr/bin/env python3
"""
metadata_reconciliation.py -- Phase 5 (task-20260724-135834,
phase_5_metadata_knowledge_consolidation), the real, re-runnable mechanism
behind ai-os/METADATA_ENGINE_RECONCILIATION_2026-07-24.yaml's own
sync_direction.mechanism: every MASTER_INDEX.yaml registries[] entry's real,
on-disk, single-file path(s) gets a knowledge_engine row tagged
registry:<id> -- the enforced back-reference from the machine-drift-detectable
half (knowledge_engine) to the human-citable half (MASTER_INDEX.yaml).

Idempotent, safe to re-run: reuses knowledge_registry_multisource.py's own
register-or-verify contract (register once, tag/verify every run after,
never a duplicate row for the same artifact_path).

Subcommands:
  reconcile  -- parse MASTER_INDEX.yaml, register/tag knowledge_engine rows,
                print the full JSON report (see METADATA_ENGINE_RECONCILIATION
                known_limitations for what is deliberately excluded).
  report     -- read-only re-check: same JSON shape, no writes.
"""
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys

import yaml

VERIDIAN_ROOT = "/opt/veridian"
SUPERBOSS = f"{VERIDIAN_ROOT}/scripts/superboss-register.py"
MASTER_INDEX_PATH = f"{VERIDIAN_ROOT}/ai-os/MASTER_INDEX.yaml"

# Real, canonical DB-path resolution -- same lazy-import-cached convention
# reconcile_stale_running_workers.py / worker-exit-status-bridge.py already use.
_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_metadata_reconciliation", SUPERBOSS)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()

PATH_TOKEN_RE = re.compile(
    r"(?:/opt/veridian/[^\s,()]+|(?<![\w/])ai-os/[^\s,()]+|(?<![\w/])scripts/[^\s,()]+|(?<![\w/])repos/[^\s,()]+)"
)
TRAILING_PUNCT_RE = re.compile(r"[.,'\"]+$")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 60), **kw)


def sb(args_list):
    proc = run(["python3", SUPERBOSS] + args_list)
    if proc.returncode != 0:
        print(f"  ! superboss-register.py {args_list[0]} failed: {proc.stderr[-500:]}")
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def row_exists(path):
    conn = sqlite3.connect(_resolve_db_path())
    row = conn.execute(
        "SELECT 1 FROM knowledge_engine WHERE artifact_path = ? ORDER BY ts DESC LIMIT 1", (path,)
    ).fetchone()
    conn.close()
    return row is not None


def all_tagged_paths():
    """Every knowledge_engine row's (artifact_path, tags) pair, for the reverse
    orphan check -- direct sqlite read, same read-only-introspection pattern
    knowledge_registry_multisource.py's own row_exists() already uses."""
    conn = sqlite3.connect(_resolve_db_path())
    rows = conn.execute("SELECT artifact_path, tags FROM knowledge_engine").fetchall()
    conn.close()
    return rows


def resolve_abs(token):
    if token.startswith("/opt/veridian/"):
        return token
    return f"{VERIDIAN_ROOT}/{token}"


def extract_file_tokens(path_field):
    """Returns a list of {"token": raw, "resolved": abs_path, "sqlite_table": str|None}
    for every real-FILE candidate this entry's path field names. Directories and
    genuinely-missing paths are reported separately by the caller, not fabricated
    here."""
    if not path_field:
        return [], []
    raw_tokens = [TRAILING_PUNCT_RE.sub("", m) for m in PATH_TOKEN_RE.findall(path_field)]
    file_hits = []
    non_file = []
    for token in raw_tokens:
        sqlite_table = None
        file_part = token
        if "#" in token:
            file_part, sqlite_table = token.split("#", 1)
        if "*" in file_part:
            base_dir = os.path.dirname(resolve_abs(file_part))
            pattern = resolve_abs(file_part)
            matches = sorted(glob.glob(pattern))
            if matches:
                for m in matches:
                    file_hits.append({"token": token, "resolved": m, "sqlite_table": sqlite_table})
            else:
                non_file.append({"token": token, "reason": "glob_no_matches", "base_dir": base_dir})
            continue
        resolved = resolve_abs(file_part)
        if os.path.isfile(resolved):
            file_hits.append({"token": token, "resolved": resolved, "sqlite_table": sqlite_table})
        elif os.path.isdir(resolved):
            non_file.append({"token": token, "reason": "directory_not_registered", "resolved": resolved})
        else:
            non_file.append({"token": token, "reason": "not_found_on_disk", "resolved": resolved})
    return file_hits, non_file


def load_registries():
    with open(MASTER_INDEX_PATH) as f:
        doc = yaml.safe_load(f)
    return doc.get("registries", [])


def reconcile(write=True):
    registries = load_registries()
    report = {
        "registries_total": len(registries),
        "registries_no_path_field": [],
        "registries_with_file_hits": 0,
        "registries_with_non_file_only": [],
        "rows_registered_new": [],
        "rows_tagged_existing": [],
        "file_hit_paths_total": 0,
    }

    for entry in registries:
        rid = entry.get("id")
        path_field = entry.get("path")
        if not path_field:
            report["registries_no_path_field"].append(rid)
            continue

        file_hits, non_file = extract_file_tokens(path_field)
        if not file_hits:
            report["registries_with_non_file_only"].append({"id": rid, "non_file": non_file})
            continue

        report["registries_with_file_hits"] += 1
        for hit in file_hits:
            resolved = hit["resolved"]
            report["file_hit_paths_total"] += 1
            existed_before = row_exists(resolved)
            if not write:
                (report["rows_tagged_existing"] if existed_before else report["rows_registered_new"]).append(
                    {"registry_id": rid, "path": resolved}
                )
                continue
            if existed_before:
                sb(["add-tag", "--path", resolved, "--tag", f"registry:{rid}"])
                report["rows_tagged_existing"].append({"registry_id": rid, "path": resolved})
            else:
                purpose = (
                    f"Reconciliation row for MASTER_INDEX.yaml registries.{rid} "
                    f"(Phase 5 metadata_engine_reconciliation, per ai-os/METADATA_ENGINE_RECONCILIATION_2026-07-24.yaml) "
                    f"-- {str(entry.get('scope'))[:300]}"
                )
                sb([
                    "register-knowledge", "--path", resolved, "--artifact-type", "canonical",
                    "--purpose", purpose, "--tags", f"registry:{rid},source:SERVER,metadata-reconciliation",
                ])
                report["rows_registered_new"].append({"registry_id": rid, "path": resolved})

    # Reverse check: knowledge_engine rows not cited by ANY registries[] entry.
    orphans = []
    for artifact_path, tags in all_tagged_paths():
        tag_list = json.loads(tags) if tags else []
        if not any(t.startswith("registry:") for t in tag_list):
            orphans.append(artifact_path)
    report["knowledge_engine_rows_not_cited_by_any_registry"] = {
        "count": len(orphans),
        "note": (
            "Expected to be non-zero and healthy -- covers VERCEL/SUPABASE/GITHUB/LOCAL multisource rows "
            "(self_sustaining_system_engine's own scope, not individually itemized in registries[]) and "
            "capability_registry-adjacent rows, not a gap by itself. Listed for visibility, not auto-remediated."
        ),
        "sample": orphans[:15],
    }
    return report


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "reconcile"
    if cmd == "reconcile":
        print(json.dumps(reconcile(write=True), indent=2))
    elif cmd == "report":
        print(json.dumps(reconcile(write=False), indent=2))
    else:
        print(json.dumps({"error": f"unknown subcommand {cmd}"}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
