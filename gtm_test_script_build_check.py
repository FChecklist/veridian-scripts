#!/usr/bin/env python3
"""gtm_test_script_build_check.py -- real, deterministic, zero-AI-call check
computing TEST_SCRIPT_BUILD for the 10-minute PM report (UMR-20260806-122546-78d6,
child of UMR-20260802-165606-4413 / OCID-020, governing scope
UMR-20260805-131542-121f, the 25 real GTM certification categories).

Answers exactly one real, mechanical question for each of the 25 real
gtm_certification_categories rows: does its real evidence_json cite a real
script_path that (a) genuinely exists as a file on disk, resolved against
either this repo's own root or the live deployed /opt/veridian/scripts/
copy, and (b) is genuinely re-runnable by an independent process -- checked
here via `py_compile` (real Python syntax/importability, not merely "a file
exists"). A narrated description standing in for a script never counts: if
evidence_json has no script_path key, or the path resolves to nothing on
disk, or the file does not compile, that category counts as a real FAIL for
this specific check -- entirely independent of the category's own
passed/fail certification verdict, which this module never reads and never
writes.

Usage (CLI, for direct/manual re-verification):
  python3 gtm_test_script_build_check.py [--json]

Library usage (generate_pm_report_v3.py's Section 2):
  from gtm_test_script_build_check import compute_test_script_build_status
  status = compute_test_script_build_status(sbr)
  # status["x"], status["total"] (== 25), status["complete"] (bool),
  # status["failing_category_indices"] (list[int])
"""
import argparse
import json
import os
import py_compile
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
LIVE_SCRIPTS_DIR = "/opt/veridian/scripts"
EXPECTED_TOTAL_CATEGORIES = 25


def _resolve_script_path(script_path_raw):
    """Real, deterministic resolution order for a script_path string pulled
    from evidence_json: absolute path as-is; else relative to this repo's
    root; else, by basename only, the live deployed scripts/ dir (covers
    evidence_json entries recorded with a 'scripts/' prefix pointing at the
    deployed copy rather than this repo). Returns the first real, existing
    regular file found, or None."""
    if not script_path_raw:
        return None
    candidates = []
    if os.path.isabs(script_path_raw):
        candidates.append(script_path_raw)
    else:
        candidates.append(os.path.join(REPO_ROOT, script_path_raw))
        candidates.append(os.path.join(LIVE_SCRIPTS_DIR, os.path.basename(script_path_raw)))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _is_genuinely_rerunnable(path):
    """Real check that `path` is independently re-runnable: valid Python
    source that py_compile can compile without raising. Deliberately does
    NOT execute the script (many gtm_check_*.py scripts have real
    side-effecting/gated behavior -- e.g. load/stress testing's hard safety
    gate) -- syntactic/importable validity is the real, safe, deterministic
    bar for "re-runnable by an independent process" this check applies."""
    try:
        with tempfile.TemporaryDirectory() as td:
            py_compile.compile(path, cfile=os.path.join(td, "out.pyc"), doraise=True)
        return True, None
    except Exception as e:
        return False, str(e)


def compute_test_script_build_status(sbr):
    """Real, deterministic, zero-AI-call computation. `sbr` is the already
    -imported superboss-register module (caller owns the canonical
    DB_PATH/_connect() resolution -- this function never re-derives it).

    Returns a dict:
      total: int (real row count read from gtm_certification_categories --
             expected 25, but never assumed/hardcoded as the row source)
      x: int (real count of categories whose evidence_json cites a real,
             existing, genuinely re-runnable script)
      complete: bool (x == total == EXPECTED_TOTAL_CATEGORIES)
      failing_category_indices: list[int] (real category_index values that
             failed the check, ascending)
      per_category: list[dict] (full real detail per category, for
             independent audit)
      error: str or None (set only on a genuine read failure -- e.g. table
             missing -- never a fabricated result)
    """
    try:
        conn = sbr._connect()
        rows = conn.execute(
            "SELECT category_index, category_name, evidence_json FROM "
            "gtm_certification_categories ORDER BY category_index"
        ).fetchall()
    except Exception as e:
        return {
            "total": 0, "x": 0, "complete": False,
            "failing_category_indices": [], "per_category": [],
            "error": f"could not read gtm_certification_categories: {e}",
        }

    per_category = []
    passing_count = 0
    failing_indices = []

    for r in rows:
        idx = r["category_index"]
        name = r["category_name"]
        raw = r["evidence_json"]
        script_path_raw = None
        json_error = None
        if raw:
            try:
                evidence = json.loads(raw)
                script_path_raw = evidence.get("script_path") if isinstance(evidence, dict) else None
            except json.JSONDecodeError as e:
                json_error = str(e)

        resolved = _resolve_script_path(script_path_raw) if script_path_raw else None
        compiles, compile_error = (_is_genuinely_rerunnable(resolved) if resolved else (False, None))

        ok = bool(resolved) and compiles
        if ok:
            passing_count += 1
        else:
            failing_indices.append(idx)

        per_category.append({
            "category_index": idx,
            "category_name": name,
            "script_path_raw": script_path_raw,
            "resolved_path": resolved,
            "exists": bool(resolved),
            "compiles": compiles,
            "compile_error": compile_error,
            "evidence_json_error": json_error,
            "ok": ok,
        })

    total = len(rows)
    return {
        "total": total,
        "x": passing_count,
        "complete": (passing_count == total == EXPECTED_TOTAL_CATEGORIES),
        "failing_category_indices": failing_indices,
        "per_category": per_category,
        "error": None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="print full machine-readable JSON instead of the human summary")
    args = ap.parse_args()

    sbr_path = os.path.join(REPO_ROOT, "superboss-register.py")
    import importlib.util
    spec = importlib.util.spec_from_file_location("superboss_register_test_script_build_check", sbr_path)
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)

    status = compute_test_script_build_status(sbr)

    if args.json:
        print(json.dumps(status, indent=2))
        return

    if status["error"]:
        print(f"ERROR: {status['error']}")
        sys.exit(1)

    print(f"TEST_SCRIPT_BUILD: {status['x']} out of {status['total']}")
    print(f"TEST_SCRIPT_BUILD_COMPLETE: {'YES' if status['complete'] else 'NO'}")
    if status["failing_category_indices"]:
        print(f"Failing category_index list: {status['failing_category_indices']}")
        for cat in status["per_category"]:
            if not cat["ok"]:
                print(f"  [{cat['category_index']:2d}] {cat['category_name']}: "
                      f"script_path_raw={cat['script_path_raw']!r} exists={cat['exists']} "
                      f"compiles={cat['compiles']}"
                      + (f" compile_error={cat['compile_error']}" if cat["compile_error"] else "")
                      + (f" evidence_json_error={cat['evidence_json_error']}" if cat["evidence_json_error"] else ""))


if __name__ == "__main__":
    main()
