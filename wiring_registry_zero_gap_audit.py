#!/usr/bin/env python3
"""wiring_registry_zero_gap_audit.py -- UMR-20260806-141055-1fec

The single deterministic Python audit script UMR-20260806-141055-1fec's own
governing SPEC required before that UMR (and its downstream task
task-20260806-193955-deterministic-final-audit--zero-gap-zero) could
genuinely close. Proves, with real SQL/filesystem query output only -- never
narration -- whether superboss-register.sqlite's wiring_registry table is:

  1. ZERO GAP           -- every real canonical file under repos/, ai-os/
                            (minus tasks/*/workspace), scripts/, shared/ has
                            a wiring_registry file/script row.
  2. ZERO DUPLICATION   -- no two DIFFERENT entity_ids share one content_hash.
  3. FIELD INTEGRITY    -- no row is missing entity_type, source_system,
                            verification_status, last_verified_ts, source_ref.
  4. RELATIONSHIP COVERAGE -- every non-root row (file/script/function/route)
                            has at least one relationship.
  5. EXTERNAL COVERAGE  -- github_repo/vercel_project/supabase_table rows are
                            real and non-zero, cross-checked against `gh`.
  6. TOTAL ENTITY COUNT -- final real row count broken down by entity_type.

Checked capability_registry (lookup-capability --intent-text over this exact
scope) and past umr_tasks for an existing tool before building this: zero
matches (see PROGRESS.md for the record) -- this is genuinely new, and gets
graduated into capability_registry as zero_gap_zero_duplication_wiring_audit,
citing UMR-20260806-141055-1fec.

Output is a single JSON object on stdout: {"ALL_CLEAR": bool, "checks": [...]}.
Per the governing SPEC: ALL_CLEAR is true only if every check below is
genuinely passing with real evidence attached inline -- a non-zero/failing
check is reported honestly (with its own real evidence and root-cause note),
never rounded up.

Usage:
    python3 wiring_registry_zero_gap_audit.py [--db PATH]
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
from collections import defaultdict

SCRIPTS = "/opt/veridian/scripts"
SUPERBOSS_REGISTER = os.path.join(SCRIPTS, "superboss-register.py")

# Real, canonical DB-path resolution -- same lazy-import-cached convention
# reconcile_stale_running_workers.py / worker-exit-status-bridge.py already use:
# never a hardcoded path, always the one real SUPERBOSS_REGISTER_DB override every
# other real caller already honors.
_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_zero_gap_audit", SUPERBOSS_REGISTER)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()

# Same 4 canonical roots + same prune list ai-os/MASTER_INDEX.yaml's
# exclusion_rules already document (node_modules, .git, ai-os/tasks/*/workspace,
# workspace/claude-cli-work, workspace/main-e2e-check) -- not a new definition
# of "canonical", the one this platform's own search tooling already uses.
CANONICAL_ROOTS = [
    "/opt/veridian/repos",
    "/opt/veridian/ai-os",
    "/opt/veridian/scripts",
    "/opt/veridian/shared",
]
PRUNE_DIR_NAMES = {"node_modules", ".git"}
PRUNE_FULL_SUBTREE_SUFFIXES = (
    "/workspace/claude-cli-work",
    "/workspace/main-e2e-check",
)

REQUIRED_WIRING_ENTITY_FIELDS = [
    "entity_type",
    "source_system",
    "verification_status",
    "last_verified_ts",
    "source_ref",
]
# Per the governing SPEC's own definition: "file, script, function, and route
# rows should always have a parent relationship; top-level repo or project
# rows are the only legitimate roots." wiring_registry's entity_type CHECK
# constraint has no 'repo'/'project' type at all, so every OTHER type
# (engine/gateway/supabase_table/cron_job/ai_role/vercel_project/github_repo/
# browser_component/dispatch_event/governance_doc) is being treated here as a
# real root/external-reference type by the SPEC's own explicit naming --
# only these 4 are checked.
NON_ROOT_ENTITY_TYPES = ("file", "script", "function", "route")

EXTERNAL_ENTITY_TYPES = ("github_repo", "vercel_project", "supabase_table")


def connect_ro(db_path):
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def walk_canonical_files():
    """Real os.walk over the 4 canonical roots, pruning exactly the
    exclusion_rules already documented in ai-os/MASTER_INDEX.yaml. Returns
    the set of real absolute file paths found."""
    real_files = set()
    for root in CANONICAL_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in PRUNE_DIR_NAMES]
            if dirpath.endswith(PRUNE_FULL_SUBTREE_SUFFIXES):
                dirnames[:] = []
                continue
            if "/ai-os/tasks/" in dirpath + "/" or dirpath.endswith("/ai-os/tasks"):
                dirnames[:] = [d for d in dirnames if d != "workspace"]
            for fname in filenames:
                real_files.add(os.path.join(dirpath, fname))
    return real_files


def check_zero_gap(conn):
    real_files = walk_canonical_files()
    rows = conn.execute(
        "SELECT path FROM wiring_registry WHERE entity_type IN ('file','script') "
        "AND path IS NOT NULL"
    ).fetchall()
    registered = {os.path.normpath(r[0]) for r in rows if r[0]}
    missing = sorted(p for p in real_files if os.path.normpath(p) not in registered)

    by_dir = defaultdict(int)
    for p in missing:
        parts = p.split("/")
        key = "/".join(parts[:5])  # e.g. /opt/veridian/repos/compliance-tracker
        by_dir[key] += 1
    top_missing_dirs = sorted(by_dir.items(), key=lambda kv: -kv[1])[:25]

    passing = len(missing) == 0
    return {
        "check": "zero_gap",
        "real_canonical_file_count": len(real_files),
        "registered_file_or_script_path_count": len(registered),
        "missing_count": len(missing),
        "passing": passing,
        "top_missing_directories_sample": top_missing_dirs,
        "sample_missing_paths": missing[:20],
        "why_nonzero": None if passing else (
            "wiring_registry's 'file' entity_type is populated by "
            "generate_wiring_registry.py's Registry.get_or_create_file(), which "
            "only creates a row when a path is CITED by another already-scanned "
            "source (capability workflow/apis paths, knowledge_engine tags, "
            "MASTER_INDEX.yaml canonical_dirs, route/engine exists_as entries, "
            "script/cron registration) -- confirmed by direct read of "
            "generate_wiring_registry.py, never called from a bare os.walk over "
            "every file under repos/ai-os/scripts/shared. This is the same "
            "reference-driven-not-exhaustive-mirror design already documented for "
            "the 20-row curated 'engine' entity_type vs the 27 *-engine.ts files "
            "physically on disk. A literal recursive-filesystem-vs-registry diff "
            "is therefore expected to be non-zero by design, not a corruption or "
            "a missed-registration bug -- closing this gap for real would require "
            "either (a) redefining 'canonical' to mean 'cited by a source', or "
            "(b) a deliberate design decision to make file registration exhaustive "
            "(a new, larger UMR scope, not a bug fix to this one)."
        ),
    }


def check_zero_duplication(conn):
    rows = conn.execute(
        """
        SELECT content_hash, GROUP_CONCAT(entity_id), COUNT(DISTINCT entity_id) AS n
        FROM wiring_registry
        WHERE content_hash IS NOT NULL AND content_hash != ''
        GROUP BY content_hash
        HAVING n > 1
        """
    ).fetchall()
    dup_groups = [
        {"content_hash": r[0], "entity_ids": r[1].split(","), "distinct_entity_count": r[2]}
        for r in rows
    ]
    total_hashed = conn.execute(
        "SELECT COUNT(*) FROM wiring_registry WHERE content_hash IS NOT NULL AND content_hash != ''"
    ).fetchone()[0]
    return {
        "check": "zero_duplication",
        "rows_with_content_hash": total_hashed,
        "duplicate_content_hash_groups": len(dup_groups),
        "passing": len(dup_groups) == 0,
        "groups_sample": dup_groups[:20],
    }


def check_field_integrity(conn):
    # source_ref/relationships default to the string '[]' (an empty JSON
    # array), not NULL/'' -- an empty array is functionally "no value", so it
    # is treated as failing here too, not just NULL/''.
    def _empty_clause(col, allow_empty_array=False):
        c = f"({col} IS NULL OR TRIM({col})='')"
        if allow_empty_array:
            c = f"({c} OR TRIM({col})='[]')"
        return c

    field_clauses = {
        "entity_type": _empty_clause("entity_type"),
        "source_system": _empty_clause("source_system"),
        "verification_status": _empty_clause("verification_status"),
        "last_verified_ts": _empty_clause("last_verified_ts"),
        "source_ref": _empty_clause("source_ref", allow_empty_array=True),
    }
    by_field = {}
    for field, clause in field_clauses.items():
        by_field[field] = conn.execute(
            f"SELECT COUNT(*) FROM wiring_registry WHERE {clause}"
        ).fetchone()[0]

    any_clause = " OR ".join(field_clauses.values())
    rows = conn.execute(
        f"SELECT entity_id, entity_type FROM wiring_registry WHERE {any_clause}"
    ).fetchall()

    return {
        "check": "field_integrity",
        "required_fields": REQUIRED_WIRING_ENTITY_FIELDS,
        "rows_failing_any_required_field": len(rows),
        "passing": len(rows) == 0,
        "by_field_null_or_empty_count": by_field,
        "sample_failing_entity_ids": [r[0] for r in rows[:20]],
    }


def check_relationship_coverage(conn):
    placeholders = ",".join("?" for _ in NON_ROOT_ENTITY_TYPES)
    rows = conn.execute(
        f"""
        SELECT entity_id, entity_type FROM wiring_registry
        WHERE entity_type IN ({placeholders})
          AND (relationships IS NULL OR TRIM(relationships)='' OR TRIM(relationships)='[]')
        """,
        NON_ROOT_ENTITY_TYPES,
    ).fetchall()
    by_type = defaultdict(int)
    for _eid, etype in rows:
        by_type[etype] += 1
    return {
        "check": "relationship_coverage",
        "non_root_entity_types_checked": list(NON_ROOT_ENTITY_TYPES),
        "rows_without_relationships": len(rows),
        "passing": len(rows) == 0,
        "by_entity_type": dict(by_type),
        "sample_entity_ids": [r[0] for r in rows[:20]],
    }


def check_external_coverage(conn):
    counts = {}
    for t in EXTERNAL_ENTITY_TYPES:
        counts[t] = conn.execute(
            "SELECT COUNT(*) FROM wiring_registry WHERE entity_type=?", (t,)
        ).fetchone()[0]

    gh_repo_names = None
    gh_error = None
    try:
        out = subprocess.run(
            ["gh", "repo", "list", "FChecklist", "--json", "name", "-L", "200"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0:
            gh_repo_names = sorted(r["name"] for r in json.loads(out.stdout))
        else:
            gh_error = out.stderr.strip()[:500]
    except Exception as e:  # pragma: no cover -- environment-dependent
        gh_error = str(e)

    registered_github_names = sorted(
        (r[0] or "").rstrip("/").split("/")[-1]
        for r in conn.execute(
            "SELECT path FROM wiring_registry WHERE entity_type='github_repo'"
        ).fetchall()
    )

    gh_cross_check = None
    if gh_repo_names is not None:
        gh_cross_check = {
            "gh_repo_list_count": len(gh_repo_names),
            "registered_github_repo_count": counts["github_repo"],
            "gh_repos_not_registered": sorted(
                set(gh_repo_names) - set(registered_github_names)
            ),
        }

    all_nonzero = all(v > 0 for v in counts.values())
    return {
        "check": "external_coverage",
        "wiring_registry_counts": counts,
        "gh_cross_check": gh_cross_check,
        "gh_error": gh_error,
        "passing": all_nonzero,
    }


def total_entity_count_by_type(conn):
    rows = conn.execute(
        "SELECT entity_type, COUNT(*) FROM wiring_registry GROUP BY entity_type ORDER BY 2 DESC"
    ).fetchall()
    return dict(rows)


def run_audit(db_path):
    conn = connect_ro(db_path)
    try:
        checks = [
            check_zero_gap(conn),
            check_zero_duplication(conn),
            check_field_integrity(conn),
            check_relationship_coverage(conn),
            check_external_coverage(conn),
        ]
        by_type = total_entity_count_by_type(conn)
    finally:
        conn.close()

    all_clear = all(c["passing"] for c in checks)
    failing = [c["check"] for c in checks if not c["passing"]]

    return {
        "umr_id": "UMR-20260806-141055-1fec",
        "db_path": db_path,
        "ALL_CLEAR": all_clear,
        "failing_checks": failing,
        "checks": checks,
        "total_entity_count": sum(by_type.values()),
        "total_entity_count_by_type": by_type,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None,
                         help="override the DB path (default: resolved via "
                              "superboss-register.py's own canonical resolver)")
    args = parser.parse_args()
    db_path = args.db if args.db is not None else _resolve_db_path()
    result = run_audit(db_path)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
