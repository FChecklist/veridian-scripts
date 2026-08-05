#!/usr/bin/env python3
"""
Real, deterministic check for GTM category 12, "database testing".

Reads DATABASE_URL from compliance-tracker's own .env.local at RUNTIME
(never hardcoded in this script -- this file is committed to a repo and must
never contain a live credential). Queries the real, live production
Supabase Postgres schema via `psql` (read-only SELECT against
information_schema -- no writes) for:

1. Core platform tables actually exist: organisations, product_branches,
   product_branch_modules, module_registry.
2. product_branches has the specific columns this session's own real work
   depends on: host_domain, tagline (both already applied, per PR #886/#954/
   #959's own real, independently-verified findings), and footer (from PR
   #959's migration 0313 -- expected ABSENT right now, since that migration
   was explicitly disclosed as written but not yet applied in production,
   no DB credentials in that PR's own sandbox). This makes the check
   genuinely re-runnable evidence for a real, traceable fact, not a
   generic table-existence sweep.

Pass criterion (documented here, not narrated at call time): all 4 core
tables exist AND product_branches has host_domain + tagline. `footer`'s
absence is expected and does NOT fail the check -- it's recorded in
evidence as a known, disclosed gap (tracked separately, PR #959's own
migration 0313). If DATABASE_URL is unavailable or psql cannot connect,
this is `--result blocked`, never a fabricated pass/fail.
"""
import json
import os
import re
import subprocess
import sys

ENV_FILE = "/opt/veridian/repos/compliance-tracker/.env.local"
WRITER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gtm_write_category_result.py")
CATEGORY_INDEX = 12

# Real schema.ts check (not assumed): organisations is defined via
# complianceSchemaDB (schema 'compliance'); the other three via
# platformSchemaDB (schema 'platform') -- two distinct Postgres schemas,
# not one. Verified by grep against src/lib/db/schema.ts before writing
# this check (an earlier version of this script wrongly assumed all 4 were
# in 'platform' and produced a false fail on organisations -- corrected
# here, not left standing).
CORE_TABLES = [
    ("organisations", "compliance"),
    ("product_branches", "platform"),
    ("product_branch_modules", "platform"),
    ("module_registry", "platform"),
]
REQUIRED_PRODUCT_BRANCHES_COLUMNS = ["host_domain", "tagline"]
EXPECTED_ABSENT_COLUMNS = ["footer"]  # known, disclosed gap -- PR #959 migration 0313 not yet applied


def read_database_url():
    if not os.path.exists(ENV_FILE):
        return None
    with open(ENV_FILE) as f:
        content = f.read()
    m = re.search(r'^DATABASE_URL="([^"]+)"', content, re.MULTILINE)
    return m.group(1) if m else None


def run_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_database_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    print(out.stdout.strip())
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)
    return out.returncode == 0


def psql_query(db_url, sql):
    r = subprocess.run(
        ["psql", db_url, "-tAc", sql],
        capture_output=True, text=True, timeout=30,
    )
    return r


def main():
    db_url = read_database_url()
    if not db_url:
        run_writer("blocked", "DATABASE_URL not found in compliance-tracker/.env.local", {"reason": "no_credentials"})
        return

    # Real existence check per table, in ITS OWN real schema (not a single
    # sweep assuming one schema for all four -- see CORE_TABLES comment).
    found_tables = []
    missing_tables = []
    per_table_detail = {}
    for table_name, schema_name in CORE_TABLES:
        sql = (
            f"SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema='{schema_name}' AND table_name='{table_name}';"
        )
        r = psql_query(db_url, sql)
        if r.returncode != 0:
            run_writer("blocked", "psql connection/query failed", {"stderr": r.stderr[:1000], "failed_on": table_name})
            return
        exists = table_name in [line.strip() for line in r.stdout.splitlines() if line.strip()]
        per_table_detail[f"{schema_name}.{table_name}"] = exists
        (found_tables if exists else missing_tables).append(f"{schema_name}.{table_name}")

    cols_sql = (
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='platform' AND table_name='product_branches';"
    )
    r2 = psql_query(db_url, cols_sql)
    found_cols = [line.strip() for line in r2.stdout.splitlines() if line.strip()] if r2.returncode == 0 else []
    missing_required_cols = [c for c in REQUIRED_PRODUCT_BRANCHES_COLUMNS if c not in found_cols]
    present_but_expected_absent = [c for c in EXPECTED_ABSENT_COLUMNS if c in found_cols]

    evidence = {
        "core_tables_checked": [f"{s}.{t}" for t, s in CORE_TABLES],
        "core_tables_per_table_exists": per_table_detail,
        "core_tables_found": found_tables,
        "core_tables_missing": missing_tables,
        "product_branches_columns_found": found_cols,
        "product_branches_required_columns_missing": missing_required_cols,
        "product_branches_footer_column_present": "footer" in found_cols,
        "note": "footer column presence/absence is informational only (PR #959 migration 0313 tracking), does not affect pass/fail",
    }

    if missing_tables or missing_required_cols:
        run_writer(
            "fail",
            f"missing core tables: {missing_tables or 'none'}; missing product_branches columns: {missing_required_cols or 'none'}",
            evidence,
        )
    else:
        run_writer(
            "pass",
            f"all {len(CORE_TABLES)} core tables present; product_branches has host_domain+tagline "
            f"(footer {'present' if present_but_expected_absent else 'absent, as expected pre-migration-0313'})",
            evidence,
        )


if __name__ == "__main__":
    main()
