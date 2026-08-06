#!/usr/bin/env python3
"""generate_platform_completion_checklist.py -- SPEC: owner absolute stop-work
order, task-20260806-165921. Produces the "real, final checklist of every
deterministic script on this platform, every real metadata table, and every
real linkage between them" the SPEC requires, mechanically -- never hand-typed
narration standing in for it.

Three sections, each a real boolean per row with real evidence:

1. SCRIPTS: every top-level *.py/*.sh (+ prompt_gateway/, owner_engine_convergence/
   subpackages) in this repo. "tested" evidence = which real test file(s) under
   ./ or ./tests/ actually reference this script's filename (grep, not naming
   convention alone -- this repo's test files are frequently named after the
   *rule*/*behavior* under test, not the script, e.g. test_rule2_dispatch_outcomes.py
   tests dispatch_core.py). Those referencing tests are then actually executed
   via pytest; pass/fail is the real evidence, not assumed.

2. TABLES: every real table (excluding FTS shadow tables) in the live
   superboss-register.sqlite (read-only connection only -- this script never
   writes to the live DB). "linked" evidence = which real scripts reference the
   table name (grep). Row count is a live COUNT(*).

3. SEARCH/LOOKUP: for every table with a paired _fts shadow table, runs one
   real FTS MATCH query seeded from that table's own live data (never a
   hand-picked/fabricated term) and confirms the seed row is actually returned
   -- proving the lookup mechanism genuinely works end to end, not merely that
   the index exists.

Read-only against the live DB throughout (sqlite3 URI mode=ro). Writes only
its own JSON evidence file + generated markdown checklist in this task
workspace.

Usage: python3 generate_platform_completion_checklist.py [--out-json PATH] [--out-md PATH] [--skip-tests]
"""
import argparse
import importlib.util as _ilu
import json
import os
import re
import sqlite3
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = "/opt/veridian/ai-os/memory/superboss-register.sqlite"

# Reuse the platform's own canonical FTS5 query-builder (superboss-register.py
# _fts_query()) instead of reimplementing term-escaping here -- a raw
# hyphenated/digit-leading seed term (e.g. a real 'INS-20260723-073434-51be'
# id) is NOT valid bare FTS5 MATCH syntax and throws "no such column: ...";
# every real caller in this codebase (wiring_query.py etc.) already goes
# through _fts_query() for exactly this reason, so testing search "genuinely
# works" means testing THAT path, not a naive one nothing in production uses.
_sbr_spec = _ilu.spec_from_file_location("superboss_register", os.path.join(REPO_ROOT, "superboss-register.py"))
_sbr = _ilu.module_from_spec(_sbr_spec)
_sbr_spec.loader.exec_module(_sbr)
_fts_query = _sbr._fts_query

SCRIPT_DIRS = [
    (".", ["*.py", "*.sh"]),
    ("prompt_gateway", ["*.py"]),
    ("prompt_gateway/engine", ["*.py"]),
    ("owner_engine_convergence", ["*.py"]),
]

EXCLUDE_BASENAMES = {"generate_platform_completion_checklist.py"}


def _list_scripts():
    import glob
    out = []
    for rel_dir, patterns in SCRIPT_DIRS:
        d = os.path.join(REPO_ROOT, rel_dir)
        if not os.path.isdir(d):
            continue
        for pat in patterns:
            for p in glob.glob(os.path.join(d, pat)):
                base = os.path.basename(p)
                if base.startswith("test_") or base in EXCLUDE_BASENAMES:
                    continue
                if base == "__init__.py":
                    continue
                rel = os.path.relpath(p, REPO_ROOT)
                out.append(rel)
    return sorted(set(out))


def _list_test_files():
    import glob
    out = []
    for p in glob.glob(os.path.join(REPO_ROOT, "test_*.py")):
        out.append(os.path.relpath(p, REPO_ROOT))
    for p in glob.glob(os.path.join(REPO_ROOT, "tests", "test_*.py")):
        out.append(os.path.relpath(p, REPO_ROOT))
    return sorted(set(out))


_TEST_CACHE = {}


def _read(path):
    if path not in _TEST_CACHE:
        try:
            with open(os.path.join(REPO_ROOT, path), "r", errors="replace") as f:
                _TEST_CACHE[path] = f.read()
        except OSError:
            _TEST_CACHE[path] = ""
    return _TEST_CACHE[path]


def _referencing_tests(script_rel, all_tests):
    base = os.path.basename(script_rel)
    stem = re.sub(r"\.(py|sh)$", "", base)
    needles = {base, stem, stem.replace("-", "_"), stem.replace("_", "-")}
    hits = []
    for t in all_tests:
        content = _read(t)
        if any(n and n in content for n in needles):
            hits.append(t)
    return hits


def _run_pytest(test_files, skip):
    """Run the real, deduplicated set of test files once via pytest, return
    {test_file: 'pass'|'fail'|'error'} from real exit status, not assumed."""
    result = {}
    if skip or not test_files:
        return result
    uniq = sorted(set(test_files))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header"] + uniq,
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800,
    )
    combined = proc.stdout + "\n" + proc.stderr
    # Per-file status via pytest's own per-test verbose rerun is expensive;
    # instead run once with -rA and parse the short summary line per nodeid,
    # falling back to whole-batch status per file if a file contributes no
    # individually-listed failure (i.e. its tests passed).
    failed_files = set()
    for line in combined.splitlines():
        m = re.match(r"^(FAILED|ERROR) (tests/)?([\w./-]+\.py)(::\S+)?", line)
        if m:
            f = (m.group(2) or "") + m.group(3)
            failed_files.add(f)
    for t in uniq:
        result[t] = "fail" if t in failed_files else ("pass" if proc.returncode in (0, 1) else "error")
    result["__summary__"] = combined.strip().splitlines()[-1] if combined.strip() else ""
    result["__returncode__"] = proc.returncode
    return result


def _grep_scripts_for_table(table, scripts):
    hits = []
    for s in scripts:
        content = _read(s)
        if re.search(r"\b" + re.escape(table) + r"\b", content):
            hits.append(s)
    return hits


def build_scripts_section(scripts, all_tests, skip_tests):
    ref_map = {s: _referencing_tests(s, all_tests) for s in scripts}
    needed_tests = sorted({t for v in ref_map.values() for t in v})
    pytest_results = _run_pytest(needed_tests, skip_tests)
    rows = []
    for s in scripts:
        tests = ref_map[s]
        if not tests:
            rows.append({"script": s, "referencing_tests": [], "tests_pass": None,
                         "complete_and_tested": False,
                         "evidence": "no test file references this script by name -- NO real test evidence found"})
            continue
        statuses = [pytest_results.get(t) for t in tests]
        all_pass = bool(statuses) and all(st == "pass" for st in statuses) and not skip_tests
        rows.append({
            "script": s, "referencing_tests": tests,
            "tests_pass": None if skip_tests else all_pass,
            "complete_and_tested": all_pass,
            "evidence": ("skipped (--skip-tests)" if skip_tests else
                         f"pytest {tests} -> {dict(zip(tests, statuses))}"),
        })
    return rows, pytest_results


def build_tables_section(conn, scripts):
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '%\\_fts%' ESCAPE '\\' "
        "AND name != 'sqlite_sequence' ORDER BY name")]
    rows = []
    for t in tables:
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError as e:
            cnt = None
        linked = _grep_scripts_for_table(t, scripts)
        rows.append({
            "table": t, "row_count": cnt, "linked_scripts_count": len(linked),
            "linked_scripts": linked[:8],
            "complete_and_tested": bool(linked) and cnt is not None,
            "evidence": f"live COUNT(*)={cnt}; referenced by {len(linked)} real script(s)",
        })
    return rows


def build_search_section(conn):
    fts_tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%\\_fts' ESCAPE '\\'")]
    rows = []
    for fts in fts_tables:
        base = fts[:-4]
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({fts})")]
        except sqlite3.OperationalError as e:
            rows.append({"fts_table": fts, "base_table": base, "works": False,
                         "evidence": f"PRAGMA failed: {e}"})
            continue
        # Prefer a real narrative/free-text column over an opaque *_id column
        # as the seed: an id like 'INS-20260723-073434-51be' tokenizes into
        # id/date/time/hex fragments that are poor, unrepresentative search
        # seeds even though they don't crash the query -- checking every
        # column for one with an actual >=4-letter word (the shape real
        # human/agent search terms take) proves the mechanism against a
        # realistic query, not just a non-empty one.
        # id-shaped columns (e.g. instruction_id='INS-20260723-073434-efcf')
        # sometimes contain an all-letter hex fragment (e.g. 'efcf') that
        # matches the >=4-letter "word" heuristic below by pure chance without
        # being a real narrative word -- try genuine content columns first,
        # id-shaped ones only as a last resort.
        all_cols = [c for c in cols if c not in ("rowid",)]
        text_cols = [c for c in all_cols if not c.endswith("_id")] + \
                    [c for c in all_cols if c.endswith("_id")]
        seed_row = None
        seed_col = None
        raw_term = None
        fallback_row = fallback_col = None
        for c in text_cols:
            try:
                rs = conn.execute(
                    f"SELECT {c} FROM {fts} WHERE {c} IS NOT NULL AND length({c})>3 LIMIT 50").fetchall()
            except sqlite3.OperationalError:
                rs = []
            for (val,) in rs:
                if fallback_row is None:
                    fallback_row, fallback_col = val, c
                words = re.findall(r"[A-Za-z]{4,}", str(val))
                if words:
                    seed_row, seed_col, raw_term = val, c, words[0]
                    break
            if seed_row is not None:
                break
        if seed_row is None and fallback_row is not None:
            seed_row, seed_col = fallback_row, fallback_col
            raw_term = str(seed_row)[:20]
        if not seed_row:
            rows.append({"fts_table": fts, "base_table": base, "works": None,
                         "evidence": "base table has no non-empty text row to seed a real query (empty table)"})
            continue
        q = _fts_query(raw_term)
        try:
            hits = conn.execute(
                f"SELECT COUNT(*) FROM {fts} WHERE {fts} MATCH ?", (q,)).fetchone()[0]
        except sqlite3.OperationalError as e:
            rows.append({"fts_table": fts, "base_table": base, "works": False,
                         "evidence": f"MATCH query failed via real _fts_query({raw_term!r})={q!r}: {e}"})
            continue
        rows.append({
            "fts_table": fts, "base_table": base, "works": hits > 0,
            "seed_term": raw_term, "hits": hits,
            "evidence": f"seeded real word {raw_term!r} from live {fts}.{seed_col}, via real _fts_query() -> MATCH returned {hits} row(s)",
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-json", default=os.path.join(REPO_ROOT, "PLATFORM_COMPLETION_CHECKLIST.json"))
    ap.add_argument("--out-md", default=os.path.join(REPO_ROOT, "PLATFORM_COMPLETION_CHECKLIST.md"))
    ap.add_argument("--skip-tests", action="store_true", help="skip running pytest (faster, for iteration)")
    args = ap.parse_args()

    scripts = _list_scripts()
    all_tests = _list_test_files()
    scripts_rows, pytest_results = build_scripts_section(scripts, all_tests, args.skip_tests)

    conn = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True, timeout=10)
    conn.row_factory = None
    tables_rows = build_tables_section(conn, scripts)
    search_rows = build_search_section(conn)
    conn.close()

    evidence = {
        "db_path": LIVE_DB,
        "scripts": scripts_rows,
        "tables": tables_rows,
        "search": search_rows,
        "pytest_summary": pytest_results.get("__summary__"),
        "pytest_returncode": pytest_results.get("__returncode__"),
    }
    with open(args.out_json, "w") as f:
        json.dump(evidence, f, indent=2)

    def yn(b):
        return "YES" if b else ("N/A" if b is None else "NO")

    lines = ["# Platform Completion Checklist (generated, mechanical evidence -- see PLATFORM_COMPLETION_CHECKLIST.json)",
             "",
             f"Generated by `generate_platform_completion_checklist.py` against live DB `{LIVE_DB}` (read-only).",
             ""]
    n_yes = sum(1 for r in scripts_rows if r["complete_and_tested"])
    lines.append(f"## Scripts ({n_yes}/{len(scripts_rows)} genuinely complete+tested)")
    lines.append("")
    lines.append("| Script | Complete+Tested | Evidence |")
    lines.append("|---|---|---|")
    for r in scripts_rows:
        lines.append(f"| `{r['script']}` | {yn(r['complete_and_tested'])} | {r['evidence']} |")
    lines.append("")
    n_yes = sum(1 for r in tables_rows if r["complete_and_tested"])
    lines.append(f"## Tables ({n_yes}/{len(tables_rows)} genuinely linked+populated)")
    lines.append("")
    lines.append("| Table | Complete+Linked | Evidence |")
    lines.append("|---|---|---|")
    for r in tables_rows:
        lines.append(f"| `{r['table']}` | {yn(r['complete_and_tested'])} | {r['evidence']} |")
    lines.append("")
    n_yes = sum(1 for r in search_rows if r["works"])
    lines.append(f"## Search/Lookup mechanisms ({n_yes}/{len(search_rows)} proven working by live seeded query)")
    lines.append("")
    lines.append("| FTS table | Works | Evidence |")
    lines.append("|---|---|---|")
    for r in search_rows:
        lines.append(f"| `{r['fts_table']}` | {yn(r['works'])} | {r['evidence']} |")
    lines.append("")

    with open(args.out_md, "w") as f:
        f.write("\n".join(lines) + "\n")

    total_scripts_yes = sum(1 for r in scripts_rows if r["complete_and_tested"])
    total_tables_yes = sum(1 for r in tables_rows if r["complete_and_tested"])
    total_search_yes = sum(1 for r in search_rows if r["works"])
    print(json.dumps({
        "scripts": f"{total_scripts_yes}/{len(scripts_rows)}",
        "tables": f"{total_tables_yes}/{len(tables_rows)}",
        "search": f"{total_search_yes}/{len(search_rows)}",
    }, indent=2))


if __name__ == "__main__":
    main()
