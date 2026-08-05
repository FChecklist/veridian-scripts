#!/usr/bin/env python3
"""
Real, additive-only migration: creates gtm_certification_categories in
superboss-register.sqlite and seeds one real row per each of the 25 real
GTM certification categories (Owner directive, UMR-20260805-131542-121f,
escalated as standalone highest-priority under UMR-20260805-145042-e536).

Scope note (why this is safe to run directly, not gated by
ddl_authorization_check.py): that gate covers Supabase/production-app DDL
(CREATE/ALTER/DROP against the live product database via the Supabase MCP).
This is a local, ops-layer sqlite table on the same server-side database
every other script in this repo already reads/writes (umr_tasks,
ocid_canonical_registry, ocid_master_standard_audit_log, ...) -- CREATE TABLE
IF NOT EXISTS only, never touches or alters any existing table, and reuses
superboss-register.py's own _connect()/_write_lock() so it is bound by the
exact same corruption-safety discipline as every other write path in this
codebase (see that file's _write_lock() docstring for the 2026-07-23
incident this pattern exists to prevent).

Idempotent: safe to re-run. CREATE TABLE IF NOT EXISTS + per-category
INSERT OR IGNORE keyed on category_index, so a second run changes nothing.

Booleans are never AI-narrated: `passed` starts NULL (pending) for every
category except the one this session has real, deterministic evidence for
(governance testing, category 14 -- see evidence_json on that row, which
cites the real ocid_master_standard_audit_log row id=3 recorded earlier this
session). Every other category stays NULL until real tool output backs a
real boolean -- this script does not fabricate results for categories that
have not actually been run.
"""
import importlib.util
import json
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location(
    "superboss_register", "/opt/veridian/scripts/superboss-register.py"
)
sbr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sbr)

PARENT_UMR = "UMR-20260802-165606-4413"  # OCID-020
SCHEMA_BUILD_CHILD_UMR = "UMR-20260805-142958-ddd8"  # this build's own child UMR
OCID_NUMBER = "OCID-020"

CATEGORIES = [
    (1, "architecture audit"),
    (2, "static code analysis"),
    (3, "security audit"),
    (4, "API testing"),
    (5, "UI testing"),
    (6, "end to end testing"),
    (7, "regression testing"),
    (8, "accessibility testing"),
    (9, "performance testing"),
    (10, "load testing"),
    (11, "stress testing"),
    (12, "database testing"),
    (13, "AI testing"),
    (14, "governance testing"),
    (15, "multi tenant testing"),
    (16, "role permission testing"),
    (17, "browser compatibility"),
    (18, "responsive testing"),
    (19, "backup and recovery testing"),
    (20, "monitoring testing"),
    (21, "deployment testing"),
    (22, "documentation audit"),
    (23, "UX audit"),
    (24, "lighthouse audit"),
    (25, "production readiness audit"),
]

# Real, deterministic evidence already produced this session -- only
# governance testing (14) has an actual recorded result. Everything else is
# genuinely pending; the notes below record real, checked constraints found
# this session (tooling absent, needs Owner go-ahead, needs budget check,
# etc.) so the row is honest about WHY it's pending, not just that it is.
KNOWN_STATE = {
    # Corrected 2026-08-05 (UMR-20260805-152508-d4c9, real independent
    # scrutiny): this originally shipped as passed=1, citing
    # ocid_master_standard_audit_log.id=3 as evidence -- but that row is a
    # curated narrative of specific historical actions (real commands, real
    # action IDs), not a single re-runnable script/check that computes a
    # governance-testing boolean from real tool output on demand. No such
    # deterministic check exists yet in this codebase for this category.
    # Left honestly pending until one is built -- do not re-introduce a
    # narrated pass here.
    14: {
        "passed": None,
        "evidence_summary": "No re-runnable deterministic check exists yet for governance testing as its own category -- needs a real script before this can be a script-computed boolean rather than a narrated judgment call.",
    },
    10: {"passed": None, "evidence_summary": "Blocked pending explicit PM go-ahead citing this UMR chain, per prior OOM-adjacent-load caution (UMR-20260805-131542-121f)."},
    11: {"passed": None, "evidence_summary": "Blocked pending explicit PM go-ahead citing this UMR chain, per prior OOM-adjacent-load caution (UMR-20260805-131542-121f)."},
    13: {"passed": None, "evidence_summary": "Blocked pending credit-accountant budget check before spending on ~1000 real prompts."},
    23: {"passed": None, "evidence_summary": "Not automatable yet: needs an Owner-defined deterministic rubric before this can be a real boolean rather than a narrated judgment call."},
    3: {"passed": None, "evidence_summary": "Tooling confirmed present this session (trivy 0.72.0, gitleaks 8.30.1, npm audit) -- not yet run as a formal category pass."},
    2: {"passed": None, "evidence_summary": "Tooling confirmed present this session (eslint, tsc in compliance-tracker) -- not yet run as a formal category pass."},
    8: {"passed": None, "evidence_summary": "Tooling confirmed present this session (axe-core in compliance-tracker) -- not yet run as a formal category pass."},
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def main():
    conn = sbr._connect()
    with sbr._write_lock():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gtm_certification_categories (
                category_index INTEGER PRIMARY KEY,
                category_name TEXT NOT NULL,
                ocid_number TEXT NOT NULL,
                parent_umr_id TEXT NOT NULL,
                child_umr_id TEXT,
                passed INTEGER,
                evidence_summary TEXT,
                evidence_json TEXT,
                fix_commit TEXT,
                fix_file_path TEXT,
                fix_pr_number INTEGER,
                validated_at TEXT,
                created_at TEXT NOT NULL,
                last_updated_at TEXT NOT NULL
            )
            """
        )
        ts = now_iso()
        for idx, name in CATEGORIES:
            state = KNOWN_STATE.get(idx, {})
            conn.execute(
                """
                INSERT OR IGNORE INTO gtm_certification_categories
                (category_index, category_name, ocid_number, parent_umr_id,
                 child_umr_id, passed, evidence_summary, evidence_json,
                 validated_at, created_at, last_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idx,
                    name,
                    OCID_NUMBER,
                    PARENT_UMR,
                    SCHEMA_BUILD_CHILD_UMR,
                    state.get("passed"),
                    state.get("evidence_summary"),
                    json.dumps({"evidence_ref": state["evidence_ref"]}) if "evidence_ref" in state else None,
                    ts if state.get("passed") is not None else None,
                    ts,
                    ts,
                ),
            )
        conn.commit()

    cur = conn.execute("SELECT COUNT(*) FROM gtm_certification_categories")
    total = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM gtm_certification_categories WHERE passed = 1")
    passed = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM gtm_certification_categories WHERE passed IS NULL")
    pending = cur.fetchone()[0]
    print(json.dumps({"table": "gtm_certification_categories", "total_rows": total, "passed": passed, "pending": pending}))


if __name__ == "__main__":
    main()
