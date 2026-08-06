#!/usr/bin/env python3
"""
verify_registry_file_paths.py -- UMR-20260806-124936-13b1, amendment to
UMR-20260806-124055-bc80 / -124327-6ffb / -124654-a8d6 (owner "stop work
order" cascade). Real, deterministic, right-now disk check for every real
row in the three tables the SPEC names: capability_registry, wiring_registry,
and the new UMR-scoped ai_agent_registry table -- never assumed, never a
relative/partial path treated as sufficient.

Reuses existing verification logic rather than reimplementing it:
  - wiring_registry: generate_wiring_registry.py's own normalize_path()/
    path_exists() (same VERIDIAN_ROOT-relative resolution convention that
    module's own `path` column generation already uses) -- but this script
    re-checks LIVE, right now, never trusts the DB's own cached
    verification_status column (that column can be stale -- see
    build_from_knowledge_engine()'s own "reused VERBATIM" docstring; this
    session found real knowledge_engine rows over a week stale where the
    file now exists again).
  - capability_registry: no dedicated path/verification_status column
    exists there. Path candidates are extracted from the free-text `apis`
    list, `workflow` field, and `business_rules[].mechanism_path` -- same
    two path conventions used everywhere else in this codebase (absolute
    `/opt/veridian/...`, or root-relative `repos/...`/`scripts/...`,
    resolved against VERIDIAN_ROOT the same way generate_wiring_registry.py
    resolves them). A capability row with zero extractable path candidates
    is its own distinct failure class (NO_PATH_FIELD), per the SPEC's
    "every real row ... must carry a real absolute file path".
  - ai_agent_registry: checks the real `memory_file_path` column per row
    (added by UMR-20260806-121332-6ba4/ai_agent_registry.py, merged this
    session via PR #199).

Usage: python3 verify_registry_file_paths.py [--json]
Exit code: 0 if every row passes, 1 if any row fails (script-consumer
friendly, e.g. for wiring into generate_platform_completion_checklist.py).
"""
import argparse
import importlib.util
import json
import os
import re
import sys

SCRIPTS_DIR = "/opt/veridian/scripts"
VERIDIAN_ROOT = "/opt/veridian"

_gwr = None
_sbr = None


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _generate_wiring_registry():
    global _gwr
    if _gwr is None:
        _gwr = _load("generate_wiring_registry_verify", "generate_wiring_registry.py")
    return _gwr


def _superboss_register():
    global _sbr
    if _sbr is None:
        _sbr = _load("superboss_register_verify", "superboss-register.py")
    return _sbr


URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def _first_token(s):
    """apis entries sometimes carry a trailing CLI subcommand
    ("scripts/document_engine.py detect-duplicates") -- the path is always
    the first whitespace-separated token, never the whole string."""
    return s.strip().split()[0] if s and s.strip() else None


def _path_before_paren(s):
    """workflow entries sometimes carry trailing "(functionName/other)"
    annotation after the real path -- strip it, never treat the
    parenthesized function-name text as part of the path."""
    if not s:
        return None
    head = s.split(" (", 1)[0].strip()
    return _first_token(head)


def extract_capability_path_candidates(cap):
    """Every real path-shaped string this capability_registry row carries,
    deduplicated, in the order apis -> workflow -> business_rules. Skips
    URLs (not a disk path)."""
    candidates = []
    for a in cap.get("apis") or []:
        p = _first_token(a)
        if p:
            candidates.append(p)
    wf = _path_before_paren(cap.get("workflow"))
    if wf:
        candidates.append(wf)
    for rule in cap.get("business_rules") or []:
        if isinstance(rule, dict) and rule.get("mechanism_path"):
            p = _first_token(rule["mechanism_path"])
            if p:
                candidates.append(p)
    seen = set()
    out = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def resolve_path(p):
    if URL_RE.match(p):
        return None  # not a disk path -- checked/reported separately, never silently "exists"
    return p if p.startswith("/") else os.path.join(VERIDIAN_ROOT, p)


def check_capability_registry(conn):
    gwr = _generate_wiring_registry()
    rows = conn.execute(
        "SELECT capability_id, capability_name, apis, workflow, business_rules FROM capability_registry"
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        cap = {
            "capability_id": d["capability_id"],
            "capability_name": d["capability_name"],
            "apis": json.loads(d["apis"]) if d["apis"] else [],
            "workflow": d["workflow"],
            "business_rules": json.loads(d["business_rules"]) if d["business_rules"] else [],
        }
        candidates = extract_capability_path_candidates(cap)
        if not candidates:
            results.append({
                "table": "capability_registry", "id": cap["capability_id"],
                "name": cap["capability_name"], "status": "NO_PATH_FIELD",
                "path": None, "resolved_path": None,
            })
            continue
        checked = []
        any_pass = False
        for c in candidates:
            resolved = resolve_path(c)
            if resolved is None:
                checked.append({"path": c, "resolved_path": None, "exists": None, "reason": "URL, not a disk path"})
                continue
            exists = gwr.path_exists(c)  # gwr.path_exists() itself calls normalize_path()
            checked.append({"path": c, "resolved_path": resolved, "exists": exists})
            any_pass = any_pass or exists
        status = "PASS" if any_pass else "FAIL"
        results.append({
            "table": "capability_registry", "id": cap["capability_id"],
            "name": cap["capability_name"], "status": status,
            "path": candidates[0], "resolved_path": resolve_path(candidates[0]),
            "all_candidates": checked,
        })
    return results


# entity_types whose `path` column is, by this schema's own design, a
# SINGLE real disk path (see WIRING_ENGINE_SCHEMA_2026-07-25.yaml /
# generate_wiring_registry.py's own build_* functions). The rest store a
# summary/identifier string that was never meant to be existence-checked
# directly -- checking those literally is the same category-error this
# session's own memory already flagged once (multi-path "a;b;c" summaries
# for engine/gateway, "src -> dst" pairs for route, crontab command lines
# for cron_job, a bare slug for ai_role, a "schema.table" identifier for
# supabase_table -- none of those strings is itself a filesystem path, even
# though the REAL underlying files they summarize are already tracked
# individually as their own 'file'/'function'/'browser_component' rows).
SINGLE_PATH_ENTITY_TYPES = {"function", "script", "governance_doc", "file", "browser_component", "github_repo"}
NOT_A_SINGLE_PATH_ENTITY_TYPES = {"engine", "gateway", "supabase_table", "ai_role", "route", "cron_job", "vercel_project", "dispatch_event"}


def check_wiring_registry(conn):
    """Live, right-now re-check of every non-NULL path on the entity_types
    whose `path` column is a single real disk path -- never trusts the DB's
    own cached verification_status (see module docstring: that column is
    reused VERBATIM from knowledge_engine for 'file' entities, which this
    session found stale by 1-13 days on real rows)."""
    gwr = _generate_wiring_registry()
    rows = conn.execute("SELECT entity_id, entity_type, path FROM wiring_registry WHERE path IS NOT NULL AND path != ''").fetchall()
    results = []
    for r in rows:
        d = dict(r)
        p = d["path"]
        if d["entity_type"] not in SINGLE_PATH_ENTITY_TYPES:
            results.append({
                "table": "wiring_registry", "id": d["entity_id"], "name": d["entity_type"],
                "status": "NOT_A_DISK_PATH", "path": p, "resolved_path": None,
            })
            continue
        if "#" in p:
            # Fragment-anchored reference into one real file (e.g.
            # MASTER_INDEX.yaml#registries.x) -- the base file's existence is
            # the real disk-path question; the fragment is a content-drift
            # concern generate_pm_report_v3.py Section 16 already tracks
            # separately (HASH_DRIFTED), never a broken-path question here.
            base = p.split("#", 1)[0]
            exists = gwr.path_exists(base)
        elif URL_RE.match(p):
            exists = None  # not a disk path -- flagged, never silently passed
        else:
            exists = gwr.path_exists(p)
        status = "PASS" if exists else ("NOT_A_DISK_PATH" if exists is None else "FAIL")
        results.append({
            "table": "wiring_registry", "id": d["entity_id"], "name": d["entity_type"],
            "status": status, "path": p, "resolved_path": gwr.normalize_path(p),
        })
    return results


def check_ai_agent_registry(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ai_agent_registry'"
    ).fetchone()
    if not cur:
        return []
    rows = conn.execute("SELECT agent_id, umr_id, memory_file_path FROM ai_agent_registry").fetchall()
    results = []
    for r in rows:
        d = dict(r)
        exists = os.path.isfile(d["memory_file_path"]) if d["memory_file_path"] else False
        status = "PASS" if exists else ("NO_PATH_FIELD" if not d["memory_file_path"] else "FAIL")
        results.append({
            "table": "ai_agent_registry", "id": d["agent_id"], "name": d["umr_id"],
            "status": status, "path": d["memory_file_path"], "resolved_path": d["memory_file_path"],
        })
    return results


def run():
    sbr = _superboss_register()
    conn = sbr._connect()
    sbr._ensure_capability_registry_table(conn)
    sbr._ensure_wiring_registry_table(conn)

    cap_results = check_capability_registry(conn)
    wiring_results = check_wiring_registry(conn)
    agent_results = check_ai_agent_registry(conn)
    conn.close()

    all_results = cap_results + wiring_results + agent_results
    summary = {}
    for table in ("capability_registry", "wiring_registry", "ai_agent_registry"):
        rows = [r for r in all_results if r["table"] == table]
        passed = sum(1 for r in rows if r["status"] == "PASS")
        failed = [r for r in rows if r["status"] not in ("PASS", "NOT_A_DISK_PATH")]
        summary[table] = {
            "total_checked": len(rows),
            "pass": passed,
            "not_a_disk_path": sum(1 for r in rows if r["status"] == "NOT_A_DISK_PATH"),
            "fail": len(failed),
            "failing_rows": failed,
        }
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output only")
    args = ap.parse_args()

    summary = run()
    total_fail = sum(t["fail"] for t in summary.values())

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        for table, s in summary.items():
            print(f"{table}: {s['pass']}/{s['total_checked']} PASS, {s['fail']} FAIL"
                  + (f", {s['not_a_disk_path']} not-a-disk-path (skipped)" if s["not_a_disk_path"] else ""))
            for f in s["failing_rows"]:
                print(f"  FAIL {f['id']} ({f.get('name')}): status={f['status']} path={f.get('path')!r} resolved={f.get('resolved_path')!r}")
        print(f"\nTOTAL real failing rows across all 3 tables: {total_fail}")

    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
