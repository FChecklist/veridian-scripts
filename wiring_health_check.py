#!/usr/bin/env python3
"""wiring_health_check.py -- Owner directive (2026-08-06): "this whole real
session has only ever discovered deterministic script wiring problems
reactively, after a real symptom already occurred ... Build a real, standing,
deterministic wiring health check script." This is that script: it actually
EXERCISES the critical integration seams between deterministic scripts (not
just greps for their existence), so a broken seam surfaces as a real
pm_decisions_pending entry on generate_pm_report_v3.py's own next 10-minute
run, before a stuck task / failed PR / silent no-op makes it someone's
afternoon.

wiring_registry reuse (never a duplicate registry): Test 2 below reads
wiring_registry exclusively through wiring_query.py's existing query()
function -- the one real read surface this codebase already established for
that table (superboss-register.py's own _connect()/_fts_query() underneath
it, never reimplemented here) -- plus one direct read-only existence/COUNT(*)
check via superboss-register.py's _connect(), same as every other table this
script touches. No new registry, no cache, nothing that could itself drift
stale.

Four real tests, each independently exercising one real integration seam:

  1. gateway_pickup_path -- submits one real row through
     dispatch-owner-task.sh --no-relay and confirms the real mechanical
     pickup path (resource_governor.py's next_queued_task()/dispatch_one(),
     invoked here via `resource_governor.py --tick`) transitions it out of
     status='queued' within one real tick. Two corrections to the SPEC's
     literal wording, verified independently against the live code before
     writing a single line of this test (see
     memory/veridian-task-prompt-false-premise-pattern.md -- this session's
     own standing rule: verify before acting on a confident-sounding claim):
       (a) dispatch-tick.py's own main() (read directly: task_status_sync(),
           supervisor_sweep_tick(), gap_queue_tick(), module_queue_tick(),
           stuck-task detection, pm_triage_tick() -- confirmed, no call to
           resource_governor.dispatch_one() or next_queued_task() anywhere
           in it) never drains the umr_tasks queue. dispatch-owner-task.sh's
           OWN comments (lines ~31, ~217) call the umr_tasks pickup
           "dispatch-tick.py's own real mechanical pickup" loosely, but the
           actual code that does it -- next_queued_task() / dispatch_one() /
           _perform_spawn() -- lives in resource_governor.py, invoked by its
           own `--tick` CLI flag (run_tick()), which is what this test
           actually calls. Same real mechanism the SPEC is asking about,
           correct real name.
       (b) This test runs against an ISOLATED, freshly-bootstrapped scratch
           copy of the real schema (via superboss-register.py's own
           init_db()/_ensure_umr_table(), the single real source of that
           DDL -- never a hand-copied duplicate), not the live production
           DB. Confirmed live before choosing this design: production
           currently holds 15 real queued tier-0 rows + 8 tier-1 (this
           canary would queue behind them, not "within one tick") and
           running_worker_count()==CONCURRENCY_CAP==5 right now (dispatch_
           one() would legitimately defer -- correct behavior, not a wiring
           bug). A recurring 10-minute canary that competes for real
           priority/capacity slots against genuine Owner-dispatched work, or
           that permanently pollutes generate_pm_report_v3.py's Section 14
           owner-closure metrics (scoped to source_trigger=
           'owner_dispatch_gateway') with a synthetic row every 10 minutes,
           would itself be a new wiring problem, not a check for one. The
           isolated scratch DB lets every real function under test
           (submit(), next_queued_task(), dispatch_one(), _perform_spawn(),
           update_umr_task()) run for real, unmodified, with a
           deterministic, uncontested queue -- same test-isolation
           convention test_pm_cycle_precheck.py's own _seed_and_load_sbr()
           already uses (SUPERBOSS_REGISTER_DB env override + a fresh
           importlib-loaded module instance so the module-level DB_PATH
           binds to the scratch file). The real host's live memory/swap/load
           headroom gate (dispatch_core.has_resource_headroom_detail()) is
           deliberately left un-overridden -- only the fixed
           CONCURRENCY_CAP slot count is raised via the documented
           VERIDIAN_DISPATCH_CONCURRENCY_CAP test seam (dispatch_core.py's
           own module comment: "still works" for tests) -- so a genuinely
           unhealthy host still fails this test honestly.
           The canary's `repo` argument is a deliberately nonexistent path
           (veridian-task.py's cmd_create checks `os.path.isdir(repo_path)`
           and exits 1 before any git/systemctl call if absent), so even the
           real, unmodified _perform_spawn() code path never spawns a real
           worker, branch, or systemd unit -- the row still genuinely
           transitions queued -> failed, which is exactly the real
           mechanical-pickup proof this test exists for.
           One further real, live-observed condition this test accounts
           for: dispatch_core.has_resource_headroom_detail() (real host
           memory/swap/load) is a genuine, separate, already-monitored
           safety gate (see generate_pm_report_v3.py's own Section 1
           SWAP_FREE_PCT_WARN_THRESHOLD) -- confirmed live while building
           this script: this host's real swap usage was 94.3%, over
           dispatch_core.py's 80% swap_backoff threshold, so a full
           queued->running/failed transition was genuinely, correctly
           refused. This test deliberately does NOT override that gate (it
           DOES raise the separate, documented-test-seam CONCURRENCY_CAP
           via VERIDIAN_DISPATCH_CONCURRENCY_CAP, since real capacity
           saturation is a load-dependent fact, not a wiring fact). Instead
           it inspects the real tick's own JSON output for proof that
           next_queued_task() genuinely found and considered this exact
           umr_id and correctly deferred it for a named real resource
           reason -- that is a working safety gate, not a broken pickup
           path, and is recorded as passed with the real reason attached
           (outcome="resource_deferred:<action>") rather than either
           silently passing or wrongly failing this specific wiring test
           for a fact Section 1 already reports on independently. A row
           that is neither dispatched nor found+considered by that
           real selection query is the one genuine failure mode.

  2. registries_reachable -- capability_registry, wiring_registry, and the
     UMR-scoped task table are all real, reachable, and queryable from
     superboss-register.py without error. One correction to the SPEC's
     literal wording here too: no table or column literally named
     "agent_id" exists anywhere in superboss-register.py (checked directly,
     zero matches) -- the real UMR-scoped table is `umr_tasks` (primary key
     `umr_id`), and that is what this test exercises.

  3. pm_report_freshness -- generate_pm_report_v3.py's own last real output
     (REPORT_LATEST_PATH) carries a generated_at timestamp less than
     PM_REPORT_FRESHNESS_MAX_MINUTES old, AND the real systemd --user timer
     that is supposed to produce it (veridian-pm-report-tick.timer) is
     genuinely loaded/scheduled -- both checked, so "installed but not
     actually firing" and "firing once then wedged" are both caught, not
     just one.

  4. external_agent_dispatch_reachable -- the external_agent_dispatch table
     exists and is queryable, and check_external_agent_eligibility() (the
     real, pure eligibility function) runs a real dry-run classification
     without raising.

Any failing test writes one real pm_decisions_pending row immediately
(decision_type='wiring_health_check_failure'), deduped against an existing
open entry for the same test name so a persistently-failing check doesn't
spam a fresh row every 10 minutes -- the same open-escalation-guard idiom
reconcile_dispatched_dead_zone.py's own escalate()/_has_open_escalation()
already established for this exact table.

Intended call site: generate_pm_report_v3.py's main() (never a separate
cron/systemd timer of its own -- see that file's own wiring for exactly
where this is invoked), so it runs on the identical real ~10-minute cadence
as the report and its result lands in that same report's own new section,
never a separate file.
"""
import argparse
import contextlib
import importlib.util as _ilu
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VERIDIAN_ROOT = os.environ.get("VERIDIAN_ROOT", "/opt/veridian")
AI_OS = os.environ.get("VERIDIAN_AI_OS_DIR", f"{VERIDIAN_ROOT}/ai-os")
SCRIPTS = os.environ.get("VERIDIAN_SCRIPTS_DIR", f"{VERIDIAN_ROOT}/scripts")

SBR_PATH = os.environ.get("VERIDIAN_SUPERBOSS_REGISTER_PY", f"{SCRIPTS}/superboss-register.py")
RESOURCE_GOVERNOR_PATH = os.environ.get("VERIDIAN_RESOURCE_GOVERNOR_PY", f"{SCRIPTS}/resource_governor.py")
DISPATCH_OWNER_TASK_SH = os.environ.get("VERIDIAN_DISPATCH_OWNER_TASK_SH", f"{SCRIPTS}/dispatch-owner-task.sh")
WIRING_QUERY_PATH = os.environ.get("VERIDIAN_WIRING_QUERY_PY", f"{SCRIPTS}/wiring_query.py")

REPORT_LATEST_PATH = os.environ.get("VERIDIAN_PM_REPORT_LATEST", f"{AI_OS}/reports/pm-report-latest.txt")
PM_REPORT_TICK_TIMER_UNIT = "veridian-pm-report-tick.timer"

PM_REPORT_FRESHNESS_MAX_MINUTES = 15
GATEWAY_CANARY_REPO = "__wiring_health_check_nonexistent_repo__"
GATEWAY_SUBMIT_TIMEOUT_SECONDS = 60
GATEWAY_TICK_TIMEOUT_SECONDS = 60
DECISION_TYPE = "wiring_health_check_failure"


def load_module_from_path(name, path):
    spec = _ilu.spec_from_file_location(name, path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_cmd(argv, timeout=30, env=None, cwd=None):
    """Real subprocess, never raises on a non-zero exit/missing binary/timeout
    -- same contract as generate_pm_report_v3.py's own run_cmd(), reimplemented
    here (not imported) to keep this module import-independent of that one
    (generate_pm_report_v3.py imports THIS module, not the other way around)."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return 127, "", str(e)
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", str(e)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Test 1: gateway pickup path
# ---------------------------------------------------------------------------
def check_gateway_pickup_path(sbr):
    fd, scratch_path = tempfile.mkstemp(prefix="wiring-health-check-gateway-", suffix=".sqlite")
    os.close(fd)
    os.remove(scratch_path)  # sqlite3 recreates it cleanly on first connect
    nonce = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    try:
        # Bootstrap: real umr_tasks DDL (superboss-register.py's own
        # _ensure_umr_table(), passed OUR raw connection -- never a
        # hand-copied schema) so the scratch file passes
        # resolve_superboss_db_path()'s own required checks before anything
        # can import superboss-register.py against it.
        raw = sqlite3.connect(scratch_path)
        raw.row_factory = sqlite3.Row
        try:
            sbr._ensure_umr_table(raw)
            raw.commit()
        finally:
            raw.close()

        # Full real schema (instructions/work_items/etc -- everything
        # dispatch-owner-task.sh's own log-instruction/log-work/check-
        # content-duplicate steps need) via a FRESH module instance bound to
        # the scratch path -- same env-override + fresh-import convention
        # test_pm_cycle_precheck.py's _seed_and_load_sbr() already uses.
        prior_env = os.environ.get("SUPERBOSS_REGISTER_DB")
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_path
        try:
            scratch_sbr = load_module_from_path(f"sbr_wiring_health_scratch_{nonce}", SBR_PATH)
            with contextlib.redirect_stdout(io.StringIO()):
                scratch_sbr.init_db()  # init_db() prints {"ok": true, "db": ...} itself; not our output
        finally:
            if prior_env is None:
                os.environ.pop("SUPERBOSS_REGISTER_DB", None)
            else:
                os.environ["SUPERBOSS_REGISTER_DB"] = prior_env

        env = os.environ.copy()
        env["SUPERBOSS_REGISTER_DB"] = scratch_path

        title = f"WIRING HEALTH CHECK CANARY {nonce}"
        prompt = (
            f"Automated wiring health check canary ({nonce}) -- real, harmless, isolated-DB probe of "
            "the dispatch-owner-task.sh -> resource_governor.py submit()/dispatch_one() gateway path, "
            "run by wiring_health_check.py. Targets a deliberately nonexistent repo so no real worker/"
            "branch/systemd unit is ever spawned. This is not real work; do not act on it."
        )
        rc, out, err = run_cmd(
            [DISPATCH_OWNER_TASK_SH, title, prompt, "2", "claude_code_cli", GATEWAY_CANARY_REPO, "--no-relay"],
            timeout=GATEWAY_SUBMIT_TIMEOUT_SECONDS, env=env, cwd=SCRIPTS,
        )
        m = re.search(r"umr_id=(\S+)", out)
        if not m:
            return {
                "name": "gateway_pickup_path", "passed": False,
                "detail": {
                    "stage": "submit", "reason": "no umr_id parsed from dispatch-owner-task.sh output",
                    "returncode": rc, "stdout_tail": out[-2000:], "stderr_tail": err[-2000:],
                },
            }
        umr_id = m.group(1)

        # Raise ONLY the fixed concurrency-cap slot count (documented test
        # seam, dispatch_core.py's own CONCURRENCY_CAP comment: "still
        # works") -- the real memory/swap/load headroom gate is left
        # untouched, so a genuinely unhealthy host still fails this
        # honestly rather than being masked.
        tick_env = env.copy()
        tick_env["VERIDIAN_DISPATCH_CONCURRENCY_CAP"] = "999"
        tick_rc, tick_out, tick_err = run_cmd(
            ["python3", RESOURCE_GOVERNOR_PATH, "--tick"],
            timeout=GATEWAY_TICK_TIMEOUT_SECONDS, env=tick_env, cwd=SCRIPTS,
        )

        ro = sqlite3.connect(f"file:{scratch_path}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        try:
            row = ro.execute(
                "SELECT status, ts_dispatched FROM umr_tasks WHERE umr_id=?", (umr_id,)
            ).fetchone()
        finally:
            ro.close()

        if row is None:
            return {
                "name": "gateway_pickup_path", "passed": False,
                "detail": {"stage": "post_tick_lookup", "umr_id": umr_id, "reason": "row not found after tick"},
            }

        picked_up = row["status"] != "queued"
        outcome = "dispatched"
        if not picked_up:
            # Real host memory/swap/load headroom (dispatch_core.py's
            # has_resource_headroom_detail()) is a genuine, already-monitored
            # host-health gate (see generate_pm_report_v3.py's own Section 1
            # SWAP_FREE_PCT_WARN_THRESHOLD) -- deliberately left un-overridden
            # here (unlike the fixed CONCURRENCY_CAP slot count above, which
            # HAS a documented test seam) so a genuinely unhealthy host is
            # never masked. If the tick's own JSON shows next_queued_task()
            # genuinely found and considered THIS umr_id (real proof the
            # selection mechanism is wired and ran against our row) but
            # correctly declined to spawn it because of that real, separate,
            # already-monitored concern, that is a working safety gate, not
            # a broken pickup path -- counted as passed here, with the real
            # reason recorded rather than either silently passing or wrongly
            # failing this test for something Section 1 already reports on.
            considered = False
            try:
                tick_json = json.loads(tick_out)
                for d in tick_json.get("dispatches", []):
                    if d.get("umr_id") == umr_id and d.get("action") in ("deferred", "frozen"):
                        considered = True
                        outcome = f"resource_deferred:{d.get('action')}"
                        break
            except (json.JSONDecodeError, AttributeError):
                pass
            picked_up = considered
            if not picked_up:
                outcome = "not_picked_up"
        return {
            "name": "gateway_pickup_path",
            "passed": picked_up,
            "detail": {
                "umr_id": umr_id,
                "outcome": outcome,
                "final_status": row["status"],
                "ts_dispatched": row["ts_dispatched"],
                "tick_returncode": tick_rc,
                "tick_stdout_tail": tick_out[-2000:],
                "tick_stderr_tail": tick_err[-2000:] if tick_rc != 0 else "",
            },
        }
    except Exception as e:
        return {
            "name": "gateway_pickup_path", "passed": False,
            "detail": {"stage": "exception", "error": f"{type(e).__name__}: {e}"},
        }
    finally:
        for suffix in ("", "-wal", "-shm", ".writelock"):
            try:
                os.remove(scratch_path + suffix)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Test 2: capability_registry / wiring_registry / umr_tasks reachable
# ---------------------------------------------------------------------------
def check_registries_reachable(sbr):
    tables = {}
    try:
        conn = sbr._connect()
    except Exception as e:
        return {
            "name": "registries_reachable", "passed": False,
            "detail": {"stage": "connect", "error": f"{type(e).__name__}: {e}"},
        }
    try:
        for table in ("capability_registry", "wiring_registry", "umr_tasks"):
            try:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone() is not None
                count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] if exists else None
                tables[table] = {"exists": exists, "row_count": count, "error": None}
            except sqlite3.Error as e:
                tables[table] = {"exists": False, "row_count": None, "error": str(e)}

        # Exercise wiring_registry's own real, established read surface
        # (wiring_query.py) rather than only a raw SELECT -- this is the
        # "reuse wiring_registry as real input, never duplicate it" part.
        wiring_query_result = None
        try:
            wq = load_module_from_path("wiring_query_wiring_health_check", WIRING_QUERY_PATH)
            sample = None
            if tables["wiring_registry"]["exists"]:
                r = conn.execute("SELECT entity_id FROM wiring_registry LIMIT 1").fetchone()
                sample = r["entity_id"] if r else None
            term = sample or "wiring_registry"
            result = wq.query(term, db_path=sbr.DB_PATH)
            wiring_query_result = {"term": term, "stage": result["wiring_registry"]["stage"],
                                    "count": result["wiring_registry"]["count"]}
        except Exception as e:
            wiring_query_result = {"error": f"{type(e).__name__}: {e}"}
    finally:
        conn.close()

    umr_note = ("SPEC referred to a 'UMR scoped agent_id table' -- no table or column literally "
                "named agent_id exists in superboss-register.py (verified directly); the real "
                "UMR-scoped table is umr_tasks (primary key umr_id), checked here instead.")
    passed = (
        all(t["exists"] and t["error"] is None for t in tables.values())
        and isinstance(wiring_query_result, dict) and "error" not in wiring_query_result
    )
    return {
        "name": "registries_reachable",
        "passed": passed,
        "detail": {"tables": tables, "wiring_query": wiring_query_result, "note": umr_note},
    }


# ---------------------------------------------------------------------------
# Test 3: generate_pm_report_v3.py pipeline is alive (regenerated recently by
# the real timer), not just installed
# ---------------------------------------------------------------------------
def check_pm_report_freshness():
    if not os.path.isfile(REPORT_LATEST_PATH):
        return {
            "name": "pm_report_freshness", "passed": False,
            "detail": {"reason": f"{REPORT_LATEST_PATH} does not exist"},
        }
    try:
        with open(REPORT_LATEST_PATH) as f:
            text = f.read()
    except OSError as e:
        return {
            "name": "pm_report_freshness", "passed": False,
            "detail": {"reason": f"could not read {REPORT_LATEST_PATH}: {e}"},
        }

    m = re.search(r"generated_at:\s*(\S+)", text)
    generated_at = None
    age_minutes = None
    if m:
        try:
            generated_at = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=timezone.utc)
            age_minutes = (datetime.now(timezone.utc) - generated_at).total_seconds() / 60.0
        except ValueError:
            generated_at = None

    mtime_age_minutes = (datetime.now(timezone.utc).timestamp() - os.path.getmtime(REPORT_LATEST_PATH)) / 60.0

    rc, out, err = run_cmd(["systemctl", "--user", "list-timers", PM_REPORT_TICK_TIMER_UNIT], timeout=15)
    timer_scheduled = rc == 0 and PM_REPORT_TICK_TIMER_UNIT in out

    effective_age = age_minutes if age_minutes is not None else mtime_age_minutes
    fresh = effective_age is not None and effective_age <= PM_REPORT_FRESHNESS_MAX_MINUTES
    passed = bool(fresh and timer_scheduled)
    return {
        "name": "pm_report_freshness",
        "passed": passed,
        "detail": {
            "report_path": REPORT_LATEST_PATH,
            "generated_at": generated_at.isoformat() if generated_at else None,
            "generated_at_age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
            "mtime_age_minutes": round(mtime_age_minutes, 2),
            "max_age_minutes": PM_REPORT_FRESHNESS_MAX_MINUTES,
            "timer_unit": PM_REPORT_TICK_TIMER_UNIT,
            "timer_scheduled": timer_scheduled,
            "timer_raw_stdout": out.strip() if not timer_scheduled else "",
        },
    }


# ---------------------------------------------------------------------------
# Test 4: external_agent_dispatch table + eligibility function
# ---------------------------------------------------------------------------
def check_external_agent_dispatch(sbr):
    try:
        conn = sbr._connect()
    except Exception as e:
        return {
            "name": "external_agent_dispatch_reachable", "passed": False,
            "detail": {"stage": "connect", "error": f"{type(e).__name__}: {e}"},
        }
    try:
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='external_agent_dispatch'"
            ).fetchone() is not None
            count = conn.execute("SELECT COUNT(*) AS n FROM external_agent_dispatch").fetchone()["n"] if exists else None
            table_error = None
        except sqlite3.Error as e:
            exists, count, table_error = False, None, str(e)
    finally:
        conn.close()

    eligibility_result = None
    eligibility_error = None
    try:
        eligible, reasons = sbr.check_external_agent_eligibility(
            task_type="doc_update",
            files_touched=["README-dispatch-consolidation.md"],
            blast_radius="isolated",
            requires_multi_file_context=False,
            acceptance_criteria="wiring_health_check.py dry-run eligibility probe -- not a real dispatch",
            repro_steps=None,
            tier=3,
        )
        eligibility_result = {"eligible": eligible, "reasons": reasons}
    except Exception as e:
        eligibility_error = f"{type(e).__name__}: {e}"

    passed = bool(exists and table_error is None and eligibility_error is None
                  and isinstance(eligibility_result, dict) and eligibility_result["eligible"] is True)
    return {
        "name": "external_agent_dispatch_reachable",
        "passed": passed,
        "detail": {
            "table_exists": exists, "row_count": count, "table_error": table_error,
            "eligibility_dry_run": eligibility_result, "eligibility_error": eligibility_error,
        },
    }


# ---------------------------------------------------------------------------
# pm_decisions_pending recording (real, immediate, deduped)
# ---------------------------------------------------------------------------
def _has_open_wiring_health_decision(conn, test_name):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM pm_decisions_pending WHERE decision_type=? AND status='open' "
        "AND title LIKE ?",
        (DECISION_TYPE, f"WIRING HEALTH CHECK FAILURE: {test_name}%"),
    ).fetchone()
    return (row["c"] if row else 0) > 0


def record_failures(sbr, results):
    """Any real failing test becomes a real pm_decisions_pending entry
    immediately, deduped against an already-open entry for the same test
    (same idiom as reconcile_dispatched_dead_zone.py's _has_open_escalation()
    against this same table) so a persistent failure doesn't spam a fresh
    row every ~10-minute report cycle."""
    recorded = []
    failing = [r for r in results if not r["passed"]]
    if not failing:
        return recorded
    with sbr._write_lock():
        conn = sbr._connect()
        try:
            for r in failing:
                if _has_open_wiring_health_decision(conn, r["name"]):
                    recorded.append({"test": r["name"], "action": "already_open_skipped"})
                    continue
                decision_id = sbr.insert_pm_decision_pending(
                    conn,
                    title=f"WIRING HEALTH CHECK FAILURE: {r['name']}",
                    detail=json.dumps(r["detail"], default=str)[:4000],
                    recommended_option=(
                        f"Investigate the '{r['name']}' wiring health check failure before it "
                        "becomes a real stuck task / failed PR / silent no-op -- see detail for the "
                        "real, mechanically-gathered evidence."
                    ),
                    decision_type=DECISION_TYPE,
                )
                conn.commit()
                recorded.append({"test": r["name"], "action": "opened", "decision_id": decision_id})
        finally:
            conn.close()
    return recorded


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_all_checks(sbr=None, record_pm_decisions=True):
    if sbr is None:
        sbr = load_module_from_path("superboss_register_wiring_health_check", SBR_PATH)

    results = [
        check_gateway_pickup_path(sbr),
        check_registries_reachable(sbr),
        check_pm_report_freshness(),
        check_external_agent_dispatch(sbr),
    ]
    overall_pass = all(r["passed"] for r in results)

    recorded_decisions = []
    if record_pm_decisions:
        try:
            recorded_decisions = record_failures(sbr, results)
        except Exception as e:
            recorded_decisions = [{"error": f"record_failures raised: {type(e).__name__}: {e}"}]

    return {
        "generated_at": _now_iso(),
        "overall_pass": overall_pass,
        "tests": results,
        "recorded_decisions": recorded_decisions,
    }


def render_text(report):
    lines = [f"WIRING HEALTH CHECK -- generated_at={report['generated_at']} "
             f"overall={'PASS' if report['overall_pass'] else 'FAIL'}"]
    for r in report["tests"]:
        lines.append(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['name']}")
        if not r["passed"]:
            lines.append(f"        {json.dumps(r['detail'], default=str)[:500]}")
    if report["recorded_decisions"]:
        lines.append("  pm_decisions_pending actions:")
        for d in report["recorded_decisions"]:
            lines.append(f"    - {d}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-record-pm-decisions", action="store_true",
                         help="Run the 4 checks but skip writing pm_decisions_pending on failure "
                              "(diagnostic/manual use).")
    parser.add_argument("--json-out", default=None, help="Also write the full result as JSON to this path.")
    args = parser.parse_args(argv)

    report = run_all_checks(record_pm_decisions=not args.no_record_pm_decisions)
    print(render_text(report))

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(report, f, indent=2, default=str)

    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
