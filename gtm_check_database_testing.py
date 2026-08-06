#!/usr/bin/env python3
"""gtm_check_database_testing.py -- real, re-runnable check for GTM
certification category_index=12 ("database testing").

Built under UMR-20260806-122546-78d6 (TEST_SCRIPT_BUILD): category 12's
evidence_json already recorded a real result (all 4 core tables present;
product_branches has host_domain+tagline) but cited a script_path,
gtm_check_database_testing.py, confirmed genuinely absent from disk. This
script reproduces that exact, real methodology as a genuine, committed,
re-runnable file, using the same DATABASE_URL / psql pattern already used
elsewhere in this repo (health-check-15min.py, cost-usage-60min.py).

What it does, every time it runs:
  1. Reads the real, live DATABASE_URL from compliance-tracker/.env.local
     (never hardcoded, never a secret checked into this repo).
  2. Runs a real, read-only `psql "$DATABASE_URL" -t -A -c "..."` against
     information_schema to confirm each of 4 real core tables exists.
  3. Runs a second real, read-only query listing platform.product_branches'
     real column set, to confirm no required column is missing.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> all 4 core tables confirmed present AND every required
           product_branches column confirmed present.
  Any real missing table/column is a genuine FAIL. "blocked" is reserved
  for: psql confirmed absent, DATABASE_URL confirmed absent from
  .env.local, or the database confirmed unreachable (connection error).

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL against gtm_certification_categories -- the read-only
information_schema queries this script itself runs against the *app*
database are the check subject, not a write to the register).

Usage:
  gtm_check_database_testing.py [--no-write]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 12
ENV_FILE = "/opt/veridian/repos/compliance-tracker/.env.local"

CORE_TABLES = [
    "compliance.organisations",
    "platform.product_branches",
    "platform.product_branch_modules",
    "platform.module_registry",
]

REQUIRED_PRODUCT_BRANCHES_COLUMNS = [
    "id", "branch_key", "display_name", "domain", "description", "is_active",
    "created_at", "tagline", "icon", "status", "launch_order", "parent_domain",
    "build_tier", "host_domain",
]


def get_env_value(key, path=ENV_FILE):
    try:
        with open(path) as f:
            for line in f:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return None


def psql(db_url, query, timeout=20):
    p = subprocess.run(
        ["psql", db_url, "-t", "-A", "-F", ",", "-c", query],
        capture_output=True, text=True, timeout=timeout,
    )
    return p.returncode, p.stdout, p.stderr


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_database_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def emit(args, result, summary, evidence):
    if args.no_write:
        print(json.dumps({"result": result, "summary": summary, "evidence": evidence}, indent=2))
        return
    call_writer(result, summary, evidence)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true", help="evaluate only, print JSON result, never call the writer")
    args = ap.parse_args()

    if not shutil.which("psql"):
        emit(args, "blocked", "psql confirmed absent from PATH.", {"missing_tools": ["psql"]})
        return

    db_url = get_env_value("DATABASE_URL")
    if not db_url:
        emit(args, "blocked", f"DATABASE_URL confirmed absent from {ENV_FILE}.", {"env_file": ENV_FILE})
        return

    # 1. real table-existence check
    table_exists = {}
    connect_error = None
    for table in CORE_TABLES:
        schema, name = table.split(".", 1)
        query = (
            f"SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            f"WHERE table_schema = '{schema}' AND table_name = '{name}');"
        )
        try:
            rc, out, err = psql(db_url, query)
        except subprocess.TimeoutExpired:
            connect_error = "psql timed out after 20s"
            break
        except FileNotFoundError as e:
            connect_error = str(e)
            break
        if rc != 0:
            connect_error = (err or "")[-1000:]
            break
        table_exists[table] = out.strip().lower() == "t"

    if connect_error is not None:
        emit(
            args, "blocked",
            f"Real psql connection/query against DATABASE_URL failed: {connect_error}",
            {"core_tables_checked": CORE_TABLES, "connect_error": connect_error},
        )
        return

    core_tables_found = [t for t, ok in table_exists.items() if ok]
    core_tables_missing = [t for t, ok in table_exists.items() if not ok]

    # 2. real product_branches column check (only meaningful if the table exists)
    columns_found = []
    if table_exists.get("platform.product_branches"):
        col_query = (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'platform' AND table_name = 'product_branches' "
            "ORDER BY ordinal_position;"
        )
        rc, out, err = psql(db_url, col_query)
        if rc == 0:
            columns_found = [c.strip() for c in out.splitlines() if c.strip()]

    columns_missing = [c for c in REQUIRED_PRODUCT_BRANCHES_COLUMNS if c not in columns_found]
    footer_present = "footer" in columns_found

    result = "pass" if (not core_tables_missing and not columns_missing) else "fail"

    evidence = {
        "core_tables_checked": CORE_TABLES,
        "core_tables_per_table_exists": table_exists,
        "core_tables_found": core_tables_found,
        "core_tables_missing": core_tables_missing,
        "product_branches_columns_found": columns_found,
        "product_branches_required_columns_missing": columns_missing,
        "product_branches_footer_column_present": footer_present,
        "note": "footer column presence/absence is informational only (PR #959 migration 0313 tracking), does not affect pass/fail",
    }
    summary = (
        f"{len(core_tables_found)}/{len(CORE_TABLES)} core tables present"
        + (f"; missing: {', '.join(core_tables_missing)}" if core_tables_missing else "")
        + (f"; product_branches missing required column(s): {', '.join(columns_missing)}" if columns_missing else "; product_branches has all required columns")
        + f" (footer {'present' if footer_present else 'absent'}, as expected pre-migration-0313)."
    )
    emit(args, result, summary, evidence)


if __name__ == "__main__":
    main()
