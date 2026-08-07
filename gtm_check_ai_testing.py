#!/usr/bin/env python3
"""gtm_check_ai_testing.py -- real, re-runnable check for GTM certification
category_index=13 ("AI testing"), OCID-020.

Prior state (2026-08-05): this category was recorded `blocked` with
evidence_summary "Blocked pending credit-accountant budget check before
spending on ~1000 real prompts." That blocker assumed a metered EXTERNAL
API (OpenRouter/Cerebras/Groq etc.) would be used to run the test prompts.
Re-authorized 2026-08-06 (UMR-20260806-100832-046d, parent
UMR-20260802-165606-4413): the real, correct mechanism is the existing
Claude Code CLI session/subagent dispatch already covered by this server's
subscription -- genuinely zero metered per-token cost -- via the SAME real
dispatch pipeline every other task on this server uses
(resource_governor.py submit -> umr_tasks queue -> the existing
veridian-cron-dispatch-tick.timer's own gated tick, which itself calls
dispatch_core.acquire_dispatch_lock()/has_free_slot()). This script never
calls OpenRouter/Cerebras/Groq or any other external paid model API, and
never invents a parallel dispatch mechanism -- it only ever submits through
resource_governor.py's real submit() entrypoint (dispatch_core.py's own
CONCURRENCY_CAP + has_resource_headroom() gate still applies, unmodified,
to whether/when the real worker actually spawns).

Design -- genuinely bounded, not ~1000-prompt scale:
Two real AI-driven test scenarios (SCENARIOS below), each dispatched as one
real `veridian_task_create` task against compliance-tracker, demonstrating
two distinct genuine "AI testing" capabilities for this project:
  1. AI-driven exploratory testing of a real target surface (find real,
     reproducible defects or honestly report none found).
  2. AI-assisted test-case generation + real execution (generate a small
     real test-case set, then really run it and report real pass/fail
     counts).
Each scenario prompt explicitly instructs the dispatched worker never to
fabricate findings, and to write real results to its own task result.json.

Re-runnable, idempotent, never fabricates:
  - Each scenario has a stable task_identity (submit()'s own dedup key) --
    re-running this script never double-submits a scenario that is already
    queued/dispatched/running.
  - Before submitting anything new, this script checks the real, canonical
    resource gate this server already uses for every dispatch decision
    (dispatch_core.has_resource_headroom(), imported directly -- never a
    reimplemented copy) plus a real veridian-worker@/veridian-supervisor@
    running-unit count against the standing 5-concurrent-unit cap. If
    resources are genuinely too tight right now, this run records
    `blocked` citing the real numbers, submits nothing, and is safe to
    re-run later once pressure subsides.
  - If scenarios were already submitted by an earlier run and are still
    queued/dispatched/running, this run records `blocked` (genuinely still
    in progress -- re-run later), never a guessed pass/fail.
  - Only once BOTH scenario tasks show a real terminal status
    (completed/failed/killed, per superboss-register.py's own
    mark-umr-terminal contract) does this script read each task's real
    result.json / outputs_json and record a real pass/fail: pass if both
    scenarios genuinely ran and produced real AI-driven-testing output
    (product defects found by the AI tester are NOT a check failure --
    this category certifies the capability to do real AI-driven testing,
    not that the product has zero bugs); fail only if a scenario's own
    dispatch/run genuinely errored out without producing real testing
    output.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=13's result.

Usage:
  gtm_check_ai_testing.py
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
RESOURCE_GOVERNOR = os.path.join(SCRIPTS_DIR, "resource_governor.py")
SBR_PATH = os.path.join(SCRIPTS_DIR, "superboss-register.py")
CATEGORY_INDEX = 13
REPO = "compliance-tracker"
TIER = 3  # low priority -- must not compete with real production/dispatch work
CONCURRENT_UNIT_CAP = 5  # standing rule: veridian-worker@*/veridian-supervisor@* cap

sys.path.insert(0, SCRIPTS_DIR)
import dispatch_core  # noqa: E402  -- reuse the real, canonical resource gate

SCENARIOS = [
    {
        "task_identity": "gtm-cat13-ai-testing-scenario-1-exploratory",
        "title": "GTM cat13 AI testing scenario 1: exploratory testing pass",
        "prompt": (
            "Real, bounded AI-driven exploratory testing task for GTM certification "
            "category 13 (AI testing), OCID-020. Target: compliance-tracker's real CRM "
            "lead-scoring page (check the real route table first in case it has moved; "
            "use the nearest real equivalent surface if so). Do exactly one focused, "
            "bounded pass: load the real page (real browser session if available, "
            "otherwise exercise its real API routes directly), exercise its real "
            "interactive elements (filters, sort, pagination, edit forms), and report "
            "REAL, independently reproducible defects found (exact steps, exact "
            "URL/route, exact expected-vs-actual). If zero real defects are found, say "
            "so explicitly -- never fabricate findings to appear more thorough. Write "
            "your real findings to this task's own result.json. This is a bounded QA "
            "pass, not an open-ended investigation -- stop and report once the one pass "
            "is complete."
        ),
    },
    {
        "task_identity": "gtm-cat13-ai-testing-scenario-2-testgen-exec",
        "title": "GTM cat13 AI testing scenario 2: AI-generated test cases + real execution",
        "prompt": (
            "Real, bounded AI-assisted test-case generation task for GTM certification "
            "category 13 (AI testing), OCID-020. Target: compliance-tracker's real "
            "invoice reconciliation flow (check the real route table first in case it "
            "has moved; use the nearest real equivalent surface if so). Generate a "
            "small, real set of test cases (aim for 5-10, not hundreds) covering its "
            "main real user-facing behaviors, then REALLY EXECUTE them using this "
            "repo's existing real test tooling (its real test suite / Playwright / "
            "direct API calls -- whichever already exists; do not invent a new test "
            "framework). Report real pass/fail counts and cite the real command(s) run. "
            "Write your real findings to this task's own result.json. This is a bounded "
            "pass, not an open-ended investigation."
        ),
    },
]

# UMR-20260806-130914-e7f1: 'completed_unmerged' added -- real, ts_completed-
# bearing terminal-for-AI-work status (see superboss-register.py's own
# UMR_STATUSES comment), added to keep this vocabulary in sync.
TERMINAL_STATUSES = {"completed", "completed_unmerged", "failed", "killed"}
IN_FLIGHT_STATUSES = {"queued", "dispatched", "running"}


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "scripts/gtm_check_ai_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def load_sbr():
    import importlib.util
    spec = importlib.util.spec_from_file_location("superboss_register", SBR_PATH)
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def existing_row(conn, task_identity):
    row = conn.execute(
        "SELECT umr_id, task_identity, status, outputs_json, ts_completed "
        "FROM umr_tasks WHERE task_identity=? ORDER BY ts_submitted DESC LIMIT 1",
        (task_identity,),
    ).fetchone()
    return dict(row) if row else None


def running_unit_count():
    p = subprocess.run(
        ["systemctl", "--user", "list-units",
         "veridian-worker@*", "veridian-supervisor@*", "--state=running", "--no-legend"],
        capture_output=True, text=True,
    )
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    return len(lines)


def real_load_and_mem():
    load1, load5, load15 = os.getloadavg()
    cpu_count = os.cpu_count() or 1
    meminfo = {}
    with open("/proc/meminfo") as f:
        for line in f:
            parts = line.split(":")
            if len(parts) == 2:
                meminfo[parts[0].strip()] = parts[1].strip()
    return {
        "load1": load1, "load5": load5, "load15": load15, "cpu_count": cpu_count,
        "meminfo_raw": {k: meminfo[k] for k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree") if k in meminfo},
    }


def main():
    sbr = load_sbr()
    conn = sbr._connect()

    rows = {s["task_identity"]: existing_row(conn, s["task_identity"]) for s in SCENARIOS}
    submitted = {k: v for k, v in rows.items() if v is not None}

    # Case 1: nothing submitted yet for one or more scenarios -- check the
    # real resource gate before submitting anything new.
    not_yet_submitted = [s for s in SCENARIOS if rows[s["task_identity"]] is None]
    if not_yet_submitted:
        res_metrics = real_load_and_mem()
        headroom_ok = dispatch_core.has_resource_headroom()
        unit_count = running_unit_count()
        cap_ok = unit_count < CONCURRENT_UNIT_CAP

        evidence = {
            "phase": "pre-submit-gate-check",
            "real_resource_metrics": res_metrics,
            "dispatch_core_has_resource_headroom": headroom_ok,
            "running_veridian_worker_or_supervisor_units": unit_count,
            "concurrent_unit_cap": CONCURRENT_UNIT_CAP,
            "cap_ok": cap_ok,
            "scenarios_not_yet_submitted": [s["task_identity"] for s in not_yet_submitted],
            "scenarios_already_submitted": {k: v["status"] for k, v in submitted.items()},
        }

        if not (headroom_ok and cap_ok):
            call_writer(
                "blocked",
                (
                    f"category 13 (AI testing): real resource gate refused new submission this "
                    f"run -- dispatch_core.has_resource_headroom()={headroom_ok}, running "
                    f"veridian-worker@/veridian-supervisor@ units={unit_count} (cap "
                    f"{CONCURRENT_UNIT_CAP}), real load1={res_metrics['load1']:.2f} on "
                    f"{res_metrics['cpu_count']} CPUs. Per standing caution (do not pile onto an "
                    f"already-stressed system), nothing was submitted this run. Genuinely "
                    f"re-runnable -- re-run this script once pressure subsides."
                ),
                evidence,
            )
            return

        # Resources genuinely OK -- submit the not-yet-submitted scenarios
        # through the real, canonical resource_governor.py submit() entrypoint.
        newly_submitted = []
        for s in not_yet_submitted:
            spec = {
                "task_identity": s["task_identity"],
                "task_kind": "veridian_task_create",
                "inputs": {"repo": REPO, "title": s["title"], "prompt": s["prompt"]},
            }
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump(spec, f)
                spec_path = f.name
            cmd = [
                sys.executable, RESOURCE_GOVERNOR, "--submit",
                "--spec-file", spec_path,
                "--tier", str(TIER),
                "--source-trigger", "gtm_check_ai_testing.py",
            ]
            p = subprocess.run(cmd, capture_output=True, text=True)
            os.unlink(spec_path)
            newly_submitted.append({
                "task_identity": s["task_identity"],
                "submit_returncode": p.returncode,
                "submit_stdout": p.stdout.strip(),
                "submit_stderr": p.stderr.strip(),
            })

        evidence["phase"] = "submitted"
        evidence["newly_submitted"] = newly_submitted
        call_writer(
            "blocked",
            (
                f"category 13 (AI testing): real resource gate passed "
                f"(has_resource_headroom={headroom_ok}, running units={unit_count}/{CONCURRENT_UNIT_CAP}); "
                f"submitted {len(newly_submitted)} real scenario task(s) via resource_governor.py "
                f"--submit through the existing subscription-covered Claude Code CLI dispatch "
                f"pipeline (no external metered API used). Genuinely in progress, not yet "
                f"complete -- re-run this script to check for real completion and record a real "
                f"pass/fail once the dispatched task(s) reach a terminal state."
            ),
            evidence,
        )
        return

    # Case 2: every scenario has been submitted at least once -- check real terminal status.
    still_in_flight = {k: v for k, v in submitted.items() if v["status"] in IN_FLIGHT_STATUSES}
    if still_in_flight:
        call_writer(
            "blocked",
            (
                f"category 13 (AI testing): all {len(SCENARIOS)} scenario(s) previously "
                f"submitted; {len(still_in_flight)} still genuinely in flight "
                f"({ {k: v['status'] for k, v in still_in_flight.items()} }). Never guessing a "
                f"pass/fail before real completion -- re-run this script later."
            ),
            {"phase": "awaiting-completion", "rows": submitted},
        )
        return

    # Case 3: every scenario reached a real terminal state -- read real outputs and decide.
    failures = []
    scenario_results = {}
    for task_identity, row in submitted.items():
        outputs = json.loads(row["outputs_json"] or "{}")
        scenario_results[task_identity] = {"status": row["status"], "outputs": outputs}
        if row["status"] != "completed" or not outputs:
            failures.append(task_identity)

    evidence = {
        "phase": "terminal",
        "scenario_results": scenario_results,
        "commit_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=SCRIPTS_DIR, capture_output=True, text=True
        ).stdout.strip(),
    }

    if failures:
        call_writer(
            "fail",
            (
                f"category 13 (AI testing): {len(failures)}/{len(SCENARIOS)} real scenario "
                f"dispatch(es) did not reach a genuine completed state with real output "
                f"({failures}). A product defect found BY a completed AI-driven test is not a "
                f"failure of this check -- this is a genuine dispatch/execution failure."
            ),
            evidence,
        )
        return

    call_writer(
        "pass",
        (
            f"category 13 (AI testing): all {len(SCENARIOS)} real, bounded AI-driven test "
            f"scenarios (1 exploratory testing pass, 1 AI-assisted test-case generation + real "
            f"execution) dispatched via the existing subscription-covered Claude Code CLI "
            f"dispatch pipeline (resource_governor.py submit, zero external metered API cost) "
            f"reached genuine completion with real output. See evidence_json.scenario_results "
            f"for each scenario's real outputs_json."
        ),
        evidence,
    )


if __name__ == "__main__":
    main()
