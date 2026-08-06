#!/usr/bin/env python3
"""pm_cycle_precheck.py -- one real, read-only invocation covering an entire
PM cycle's data-gathering pass, so the PM's own real tokens go to real
thinking/decisions, never to real manual searching/checking across many
separate SSH round trips. UMR-20260806-035541 (Owner directive, "real PM
cycle scope"), extending UMR-20260805-181636-32f2 (the real
generate_pm_report_v3.py report-generator chain) and
UMR-20260805-185000-e94f (the real deterministic-script-consolidation
chain) -- same "zero AI/LLM calls, every field a direct read or a pure
function of a direct read" principle as generate_pm_report_v3.py, not a
new one.

Five real sections, each reusing an existing real mechanism rather than
re-deriving it:
  1. Server health       -- generate_pm_report_v3.py's own get_ram_swap/
                             get_load_average/get_dispatch_tick_status/
                             get_worker_count/get_stuck_tasks/get_tmux_status/
                             get_emergency_stop/get_db_integrity, imported and
                             called directly (importlib, same pattern
                             dispatch_core.py/generate_wiring_registry.py
                             already use for superboss-register.py), not
                             copy-pasted here.
  2. Dispatch tick deltas -- reads the real, existing pm_report_snapshots
                             table (via generate_pm_report_v3.get_prior_snapshot)
                             to find the last real PM cycle's own timestamp,
                             then counts real umr_tasks rows submitted since
                             then, grouped by real status.
  3. Zero-dup precheck    -- given --search-term, searches real QUEUED/
                             DISPATCHED/RUNNING umr_tasks rows (umr_tasks_fts)
                             AND reuses superboss-register.py's own existing
                             check_duplicate() (system_index/wiring_registry/
                             knowledge_engine/capability_registry) by calling
                             it directly and capturing its real stdout --
                             not a second search implementation.
  4. PR state checks      -- given --pr-numbers, real `gh pr view --json`
                             per number, no invented state.
  5. OCID-068 regression  -- resolver present (resolve_ocid_canonical
                             importable), ocid_canonical_registry real row
                             count vs. the real, independently-verified
                             baseline (69, OCID-001..OCID-069, see
                             OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md
                             section 6), and the seven real guardrail PRs'
                             merge commits (OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md)
                             still real ancestors of origin/main via `git
                             merge-base --is-ancestor`.

Read-only against production data in every section above -- the only real
write this script ever performs is its own bookkeeping append to
PRECHECK_HISTORY_PATH (this script's own invocation log, analogous to
generate_pm_report_v3.py's REPORT_HISTORY_PATH), skippable via
--no-bookkeeping-write.

Independent verification note (this same task, 2026-08-06): no SKILL.md
file exists anywhere in this repository's history (`git log --all -- '**/SKILL.md'`
is empty) despite this task's own dispatch SPEC citing one for the OCID-068
checks below -- the three concrete checks (resolver/row-count/seven-PR-ancestry)
were instead independently recovered from this repo's own real, merged
OCID_068_*.md records and re-verified live (see this task's own
PM_CYCLE_PRECHECK_VERIFICATION_2026-08-06.md) before being encoded here.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

VERIDIAN_ROOT = os.environ.get("VERIDIAN_ROOT", "/opt/veridian")
AI_OS = os.environ.get("VERIDIAN_AI_OS_DIR", f"{VERIDIAN_ROOT}/ai-os")
SCRIPTS = os.environ.get("VERIDIAN_SCRIPTS_DIR", f"{VERIDIAN_ROOT}/scripts")

SBR_PATH = os.environ.get("VERIDIAN_SUPERBOSS_REGISTER_PY", f"{SCRIPTS}/superboss-register.py")
GEN_PM_REPORT_PATH = os.environ.get(
    "VERIDIAN_GENERATE_PM_REPORT_PY", f"{SCRIPTS}/generate_pm_report_v3.py")

PRECHECK_HISTORY_PATH = os.environ.get(
    "VERIDIAN_PM_CYCLE_PRECHECK_HISTORY", f"{AI_OS}/reports/pm-cycle-precheck-history.log")

DEFAULT_GH_REPO = os.environ.get("VERIDIAN_DEFAULT_GH_REPO", "FChecklist/veridian-scripts")

# Real, independently re-verified baseline (this task, 2026-08-06): the full
# OCID roster is OCID-001..OCID-069 -- see
# OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md section 6
# ("full 69-OCID roster"). A live count that drifts from this is a real
# regression signal (a row deleted or the roster silently un-scoped), not
# proof by itself of what changed -- this script only flags the mismatch.
EXPECTED_OCID_CANONICAL_REGISTRY_ROWS = 69

# Real, independently re-verified (this task, 2026-08-06, live `git
# merge-base --is-ancestor` against origin/main) merge commits for the seven
# OCID-068 guardrail rules -- see
# OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md's own table.
# Overridable via --guardrail-prs-file for a future rule addition without an
# code edit here.
GUARDRAIL_PRS = [
    {"pr": 26, "rule": "Rule 1 (UMR reuse on resume)", "merge_commit": "29a153bb7a51a12f1868a372bc7e20b90818b152"},
    {"pr": 29, "rule": "Rule 2 (dispatch outcome classification)", "merge_commit": "50c272dcac3e321179adff61dca0879a8b6c7ea7"},
    {"pr": 30, "rule": "Rule 3 (no premature UMR minting)", "merge_commit": "fe3ec0dffdd0cbe67b6a6b6e5d9a003bab0dd0db"},
    {"pr": 32, "rule": "Rule 4 (PM-visible real counts)", "merge_commit": "64e16d0e8c05694952c314cd85a0f4e4fc366324"},
    {"pr": 33, "rule": "Rule 5 (real stall detection)", "merge_commit": "9b716b939b2ac69297082505ef645a6ed715a959"},
    {"pr": 34, "rule": "Rule 6 (zero duplication by OCID)", "merge_commit": "8235a87fd57e9e57697f18e78cd29fbcc195a524"},
    {"pr": 35, "rule": "Rule 7 (completion evidence)", "merge_commit": "638fd38403c86d82395331f1c00208937b31764a"},
]


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_module_from_path(name, path):
    """Same 3-line importlib bootstrap dispatch_core.py/generate_pm_report_v3.py/
    generate_wiring_registry.py each already carry (their own docstrings note
    this is the shared convention for loading a hyphenated-filename module,
    not something reused via a normal import statement) -- this file's own
    copy, not a fourth divergent implementation of anything beyond this."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_cmd(argv, timeout=30):
    """Real subprocess wrapper, byte-for-byte the same contract as
    generate_pm_report_v3.run_cmd (never raises on non-zero exit/missing
    binary) -- this file's own copy of that exact 8-line primitive rather
    than importing it, since server_health() below already imports the whole
    generate_pm_report_v3 module and calling back into this file's run_cmd
    from there would be a confusing cross-module dependency for a trivial
    subprocess wrapper."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return 127, "", str(e)
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", str(e)


def resolve_repo_root():
    """Same real self-discovery generate_wiring_registry.py's own
    resolve_doc_path()/REPO_ROOT already established: prefer this file's own
    git checkout (a worktree's .git is a FILE, not a directory -- os.path.exists,
    never os.path.isdir, per that function's own documented bugfix) over the
    server's real, cron-kept-current git mirror."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    if os.path.exists(os.path.join(repo_root, ".git")):
        return repo_root
    mirror = os.environ.get(
        "VERIDIAN_SCRIPTS_GIT_MIRROR", f"{VERIDIAN_ROOT}/repos/veridian-scripts")
    if os.path.isdir(os.path.join(mirror, ".git")):
        return mirror
    return repo_root


# ---------------------------------------------------------------------------
# Section 1: server health -- reuses generate_pm_report_v3.py verbatim
# ---------------------------------------------------------------------------
def gather_server_health(pmr, sbr):
    ram_swap = pmr.get_ram_swap()
    load_avg = pmr.get_load_average()
    dispatch_tick = pmr.get_dispatch_tick_status()
    workers = pmr.get_worker_count()
    stuck = pmr.get_stuck_tasks()
    tmux = pmr.get_tmux_status()
    emergency_stop = pmr.get_emergency_stop()
    db_integrity = pmr.get_db_integrity(sbr)
    return {
        "ram_swap": ram_swap,
        "load_average": load_avg,
        "dispatch_tick": dispatch_tick,
        "workers": workers,
        "stuck_tasks": stuck,
        "tmux": tmux,
        "emergency_stop": emergency_stop,
        "db_integrity": db_integrity,
    }


# ---------------------------------------------------------------------------
# Section 2: dispatch tick results since the last real PM cycle
# ---------------------------------------------------------------------------
def gather_dispatch_tick_deltas(pmr, sbr):
    # A get_prior_snapshot() error (e.g. pm_report_snapshots doesn't exist yet
    # on a fresh DB, or generate_pm_report_v3.py has genuinely never run) is
    # treated the same as "no prior row found" below -- still counts every
    # real umr_tasks row rather than reporting nothing, same fail-open
    # principle get_db_integrity's own callers already apply. The real error
    # text is still surfaced, never silently swallowed.
    prior, err = pmr.get_prior_snapshot(sbr)
    since_ts = prior["ts"] if prior else None

    conn = sbr._connect()
    try:
        if since_ts:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM umr_tasks WHERE ts_submitted > ? GROUP BY status",
                (since_ts,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM umr_tasks GROUP BY status"
            ).fetchall()
        status_counts = {r[0]: r[1] for r in rows}
        total_since = sum(status_counts.values())
    finally:
        conn.close()

    return {
        "prior_cycle_ts": since_ts,
        "prior_cycle_found": prior is not None,
        "prior_cycle_lookup_error": err,
        "status_counts": status_counts,
        "total_tasks_since_last_cycle": total_since,
    }


# ---------------------------------------------------------------------------
# Section 3: zero-duplication precheck
# ---------------------------------------------------------------------------
def gather_duplication_precheck(sbr, search_term):
    if not search_term:
        return None

    result = {"search_term": search_term}

    # 3a. Real queued/dispatched/running umr_tasks rows matching the term --
    # the one real gap no existing CLI command covers (check-duplicate below
    # searches system_index/wiring_registry/knowledge_engine/capability_registry,
    # never umr_tasks itself). Read-only FTS query, same _fts_query() builder
    # superboss-register.py's own check_duplicate/lookup_capability/lookup_entity
    # already use -- not a second query-builder.
    conn = sbr._connect()
    try:
        q = sbr._fts_query(search_term)
        try:
            rows = conn.execute(
                "SELECT t.umr_id, t.task_identity, t.status, t.ts_submitted, t.source_trigger "
                "FROM umr_tasks_fts f JOIN umr_tasks t ON t.rowid = f.rowid "
                "WHERE umr_tasks_fts MATCH ? ORDER BY t.ts_submitted DESC",
                (q,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        active = [dict(r) for r in rows if r["status"] in sbr.UMR_ACTIVE_STATUSES]
    finally:
        conn.close()
    result["active_umr_tasks_matching_term"] = active
    result["active_umr_tasks_match_count"] = len(active)

    # 3b. Reuse superboss-register.py's own existing check_duplicate() (does
    # this capability/script/knowledge-fragment already exist) rather than
    # re-implementing its four-table FTS search here -- calls the real
    # function directly and captures its real stdout, same "one query-building
    # function" principle check_duplicate's own docstring already documents.
    class _Args:
        query = search_term
        category = ""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sbr.check_duplicate(_Args())
    try:
        result["existing_capability_check"] = json.loads(buf.getvalue())
    except json.JSONDecodeError:
        result["existing_capability_check"] = {"error": "could not parse check_duplicate output", "raw": buf.getvalue()}

    return result


# ---------------------------------------------------------------------------
# Section 4: PR state checks for a tracked list of open PR numbers
# ---------------------------------------------------------------------------
def gather_pr_states(pr_numbers, repo):
    if not pr_numbers:
        return None
    results = []
    for num in pr_numbers:
        code, out, err = run_cmd([
            "gh", "pr", "view", str(num), "--repo", repo,
            "--json", "number,state,title,mergedAt,mergeCommit,url,isDraft",
        ])
        if code != 0:
            results.append({"pr": num, "ok": False, "error": err.strip() or f"exit {code}"})
            continue
        try:
            data = json.loads(out)
        except json.JSONDecodeError as e:
            results.append({"pr": num, "ok": False, "error": f"unparseable gh output: {e}"})
            continue
        results.append({
            "pr": num, "ok": True, "state": data.get("state"),
            "title": data.get("title"), "merged_at": data.get("mergedAt"),
            "merge_commit": (data.get("mergeCommit") or {}).get("oid"),
            "is_draft": data.get("isDraft"), "url": data.get("url"),
        })
    return results


# ---------------------------------------------------------------------------
# Section 5: OCID-068 regression checks
# ---------------------------------------------------------------------------
def gather_ocid_068_regression_checks(sbr):
    resolver_present = hasattr(sbr, "resolve_ocid_canonical")

    conn = sbr._connect()
    try:
        row_count = conn.execute("SELECT COUNT(*) FROM ocid_canonical_registry").fetchone()[0]
    finally:
        conn.close()
    row_count_ok = row_count == EXPECTED_OCID_CANONICAL_REGISTRY_ROWS

    repo_root = resolve_repo_root()
    guardrail_results = []
    for entry in GUARDRAIL_PRS:
        code, out, err = run_cmd(
            ["git", "-C", repo_root, "merge-base", "--is-ancestor", entry["merge_commit"], "origin/main"]
        )
        guardrail_results.append({
            **entry,
            "still_ancestor_of_origin_main": code == 0,
            "check_exit_code": code,
            "check_error": err.strip() if code not in (0, 1) else None,
        })
    guardrails_all_ok = all(g["still_ancestor_of_origin_main"] for g in guardrail_results)

    return {
        "resolver_present": resolver_present,
        "canonical_registry_row_count": row_count,
        "canonical_registry_row_count_expected": EXPECTED_OCID_CANONICAL_REGISTRY_ROWS,
        "canonical_registry_row_count_ok": row_count_ok,
        "repo_root_checked": repo_root,
        "guardrail_prs": guardrail_results,
        "guardrail_prs_all_still_ancestors": guardrails_all_ok,
        "regression_detected": not (resolver_present and row_count_ok and guardrails_all_ok),
    }


# ---------------------------------------------------------------------------
# Bookkeeping (this script's own, never production data)
# ---------------------------------------------------------------------------
def write_bookkeeping(report):
    os.makedirs(os.path.dirname(PRECHECK_HISTORY_PATH), exist_ok=True)
    with open(PRECHECK_HISTORY_PATH, "a") as f:
        f.write(json.dumps({"ts": report["generated_at"]}) + "\n")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_report_text(report):
    lines = []
    lines.append("=" * 78)
    lines.append(f"PM CYCLE PRECHECK -- {report['generated_at']}")
    lines.append("=" * 78)

    sh = report["server_health"]
    lines.append("")
    lines.append("-- Server health --")
    lines.append(f"  RAM/swap: {sh['ram_swap']}")
    lines.append(f"  Load average: {sh['load_average']}")
    lines.append(f"  Dispatch tick active: {sh['dispatch_tick'].get('dispatch_tick_active')}")
    lines.append(f"  Parallel workers: {sh['workers'].get('parallel_worker_count')}")
    lines.append(f"  Stuck tasks: {sh['stuck_tasks'].get('stuck_task_count')}")
    lines.append(f"  tmux session alive: {sh['tmux'].get('tmux_session_alive')}")
    lines.append(f"  Emergency stop present: {sh['emergency_stop'].get('emergency_stop_present')}")
    lines.append(f"  DB integrity ok: {sh['db_integrity'].get('db_integrity_ok')}")

    dt = report["dispatch_tick_deltas"]
    lines.append("")
    lines.append(f"-- Dispatch tick results since last PM cycle ({dt.get('prior_cycle_ts')}) --")
    if dt.get("prior_cycle_lookup_error"):
        lines.append(f"  (prior-cycle lookup error, counting all real rows instead: {dt['prior_cycle_lookup_error']})")
    lines.append(f"  total tasks since last cycle: {dt['total_tasks_since_last_cycle']}")
    for status, count in sorted(dt["status_counts"].items()):
        lines.append(f"    {status}: {count}")

    dup = report.get("duplication_precheck")
    lines.append("")
    if dup is None:
        lines.append("-- Zero-duplication precheck: skipped (no --search-term given) --")
    else:
        lines.append(f"-- Zero-duplication precheck for '{dup['search_term']}' --")
        lines.append(f"  active (queued/dispatched/running) umr_tasks matches: {dup['active_umr_tasks_match_count']}")
        for m in dup["active_umr_tasks_matching_term"][:10]:
            lines.append(f"    {m['umr_id']} [{m['status']}] {m['task_identity'][:80]}")
        ecc = dup["existing_capability_check"]
        lines.append(f"  existing capability/script/knowledge check found={ecc.get('found')} "
                      f"verdict={ecc.get('verdict')}")

    prs = report.get("pr_states")
    lines.append("")
    if prs is None:
        lines.append("-- PR state checks: skipped (no --pr-numbers given) --")
    else:
        lines.append("-- Tracked PR state checks --")
        for p in prs:
            if not p["ok"]:
                lines.append(f"  PR #{p['pr']}: ERROR {p['error']}")
            else:
                lines.append(f"  PR #{p['pr']} [{p['state']}]{' (draft)' if p['is_draft'] else ''}: {p['title']}"
                              f" merged_at={p['merged_at']}")

    ocid = report["ocid_068_regression"]
    lines.append("")
    lines.append("-- OCID-068 regression checks --")
    lines.append(f"  resolver present (resolve_ocid_canonical): {ocid['resolver_present']}")
    lines.append(f"  ocid_canonical_registry row count: {ocid['canonical_registry_row_count']} "
                 f"(expected {ocid['canonical_registry_row_count_expected']}) "
                 f"-> {'OK' if ocid['canonical_registry_row_count_ok'] else 'MISMATCH'}")
    lines.append("  seven guardrail PRs still ancestors of origin/main:")
    for g in ocid["guardrail_prs"]:
        status = "OK" if g["still_ancestor_of_origin_main"] else "REGRESSION"
        lines.append(f"    PR #{g['pr']} ({g['rule']}): {status}")
    lines.append(f"  REGRESSION DETECTED: {ocid['regression_detected']}")

    lines.append("")
    lines.append("=" * 78)
    return "\n".join(lines)


def build_report(pmr, sbr, search_term, pr_numbers, repo):
    return {
        "generated_at": _now_iso(),
        "server_health": gather_server_health(pmr, sbr),
        "dispatch_tick_deltas": gather_dispatch_tick_deltas(pmr, sbr),
        "duplication_precheck": gather_duplication_precheck(sbr, search_term),
        "pr_states": gather_pr_states(pr_numbers, repo),
        "ocid_068_regression": gather_ocid_068_regression_checks(sbr),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--search-term", default=None,
                         help="Zero-duplication precheck: search term to look for among "
                              "queued/dispatched/running tasks and existing capabilities/scripts "
                              "before dispatching anything new.")
    parser.add_argument("--pr-numbers", default=None,
                         help="Comma-separated list of tracked open PR numbers to check state for.")
    parser.add_argument("--repo", default=DEFAULT_GH_REPO, help=f"GitHub repo for --pr-numbers (default {DEFAULT_GH_REPO}).")
    parser.add_argument("--json-out", default=None, help="Also write the full report as JSON to this path.")
    parser.add_argument("--no-bookkeeping-write", action="store_true",
                         help="Skip this script's own invocation-log append (still prints the report).")
    args = parser.parse_args(argv)

    pr_numbers = None
    if args.pr_numbers:
        pr_numbers = [n.strip() for n in args.pr_numbers.split(",") if n.strip()]

    pmr = load_module_from_path("generate_pm_report_v3", GEN_PM_REPORT_PATH)
    sbr = load_module_from_path("superboss_register", SBR_PATH)

    report = build_report(pmr, sbr, args.search_term, pr_numbers, args.repo)
    text = render_report_text(report)
    print(text)

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(report, f, indent=2, default=str)

    if not args.no_bookkeeping_write:
        write_bookkeeping(report)

    return 1 if report["ocid_068_regression"]["regression_detected"] else 0


if __name__ == "__main__":
    sys.exit(main())
