#!/usr/bin/env python3
"""pm_lifecycle.py -- single, real, single-command full-lifecycle task
orchestrator (task-20260814-183228-build-single-command-full-lifecycle-orch).

Real gap this closes: closing one real task today requires a PM tier to run
roughly 8-15 separate manual steps across multiple SSH round-trips (submit,
poll status, fetch PR state, fetch audit comments, decide merge/rebase/fix,
redispatch on failure, re-verify, update records) with no single mechanism
tying the whole lifecycle together -- several real dispatches this session
were redundantly re-checked or wrongly assumed complete because of exactly
that gap. This script is the one real command that does all of it.

Deliberately reuses every existing real mechanism rather than
reimplementing any of it (per this codebase's own zero-gap-zero-duplication
convention, and per this task's own deterministic briefing, which found
capability_registry rows for a DIFFERENT thing -- resource_governor.py's
run_tick() 12-step deterministic dispatch-decision pipeline -- not this
PM-facing, manually-invoked, single-task full-cycle command; that pipeline
is orthogonal and is not duplicated here):

  1. submit/classify   -- task-gateway.py submit (subprocess CLI call,
                           exactly as dispatch-owner-task.sh itself does)
  2. software-only gate -- inspects submit's own
                           capability_deterministic_path_available flag; if
                           set AND the matched capability is on this
                           script's own explicit SAFE_DETERMINISTIC_EXECUTORS
                           allow-list, runs it and stops -- never invents a
                           generic executor for an arbitrary capability row
                           (that would be blind code execution from DB
                           content). Otherwise falls through to AI dispatch.
  3. dispatch           -- dispatch-owner-task.sh (subprocess CLI call, the
                           existing single front door for real Owner-
                           directed dispatch) with a tightened, labeled
                           prompt (## OBJECTIVE / ## SCOPE / ## SUCCESS_
                           CRITERIA / ## EXPECTED_OUTPUT / ## KNOWN_CONTEXT /
                           ## COMPLEXITY_TIER -- the same labeled-field shape
                           tight_task_validation.py's own
                           validate_tight_task()/parse_labeled_fields()
                           already validate against).
  4. poll               -- resource_governor.py --query-umr --umr-id <id>
                           at a sane interval until umr_tasks.status leaves
                           UMR_ACTIVE_STATUSES (queued/dispatched/running),
                           bounded by --poll-timeout.
  5. independent verify -- never trusts the register's own status label:
                           re-derives real PR state + AUDIT:PASS/FAIL
                           comment freshness against the PR's CURRENT head
                           via reconcile_owner_dispatch_status.py's own
                           tested primitives (_task_dir_from_unit/
                           _read_task_yaml/_fetch_prs/_audit_verdict),
                           reused directly rather than re-implemented a
                           second time.
  6. audit-fail retry   -- on a real, fresh AUDIT:FAIL, dispatches ONE real
                           fix citing the specific finding (same pattern
                           pm-sentinel-tick.sh's dispatch_gap() already uses
                           manually), then re-runs step 5. Capped at
                           --max-fix-retries (default 2) -- surfaces to the
                           PM rather than looping forever.
  7. merge + re-verify  -- once AUDIT:PASS (fresh) and the PR is real and
                           OPEN, merges it (`gh pr merge --merge`, the same
                           real call supervisor-entrypoint.sh's own
                           independent-review merge gate already makes --
                           this script IS that independent reviewer at this
                           point, having just re-verified live state itself;
                           this is not the "let a worker blindly merge its
                           own PR" pattern pm-sentinel-tick.sh's AUDIT-REJECT
                           fix #4 removed), then re-fetches PR state to
                           confirm state=MERGED landed for real.
  8. record             -- writes the real, independently-derived 6-boolean
                           status (COMPLETED/TESTED/AUDITED/INTEGRATED/
                           WORKING/CERTIFIED, each with its own evidence
                           dict, no self-certification) into the umr_tasks
                           row's metadata_json, via superboss-register.py's
                           own update_umr_task() under _write_lock() (read-
                           merge-write, same convention
                           reconcile_owner_dispatch_status.py's
                           apply_correction() already uses -- never a raw
                           UPDATE, never a blind replace of metadata_json).

Dependency note (per this task's own SPEC EVIDENCE-ONLY list): of the four
sibling UMRs this task builds on top of, only UMR-20260814-180439-7462
(real file-attachment intake, task-gateway.py submit's --attach) was merged
onto this checkout's origin/main at the time this script was written
(confirmed via `git log --oneline -5`: HEAD merges PR #383, the file-
attachment intake PR). The other three (token-usage instrumentation,
search-results cache, completion-verification against gh) were not yet
merged -- this script does not block on or re-implement any of them; --attach
is wired straight through to task-gateway.py submit's real --attach, and
completion-verification is done via reconcile_owner_dispatch_status.py's
existing real gh-based primitives (see step 5 above), not a new
implementation of that not-yet-merged capability.

Usage:
    python3 pm_lifecycle.py run --title "<short title>" --text "<full task text>" \\
        --repo <repo> [--tier N] [--medium claude_code_cli|ssh_session] \\
        [--attach <path>] [--poll-interval SEC] [--poll-timeout SEC] \\
        [--max-fix-retries N] [--no-relay]

Exit codes: 0 = ran to a real terminal outcome (CERTIFIED or an honest
                non-certified terminal report -- see the printed JSON's own
                "certified" field for the real verdict).
            1 = refused before dispatch (duplicate, rejected by the queue).
            2 = poll timed out before the dispatched worker reached a
                terminal register status.
            3 = a real internal error.
"""
import argparse
import importlib.util as _ilu
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GH_ORG = os.environ.get("VERIDIAN_GH_ORG", "FChecklist")

DEFAULT_POLL_INTERVAL_SECONDS = 30
DEFAULT_POLL_TIMEOUT_SECONDS = 2400
DEFAULT_MAX_FIX_RETRIES = 2

UMR_ACTIVE_STATUSES = ("queued", "dispatched", "running")


def _load_module(filename, modname):
    """Real in-process import of another script in this directory, same
    spec_from_file_location convention already used throughout this
    codebase (reconcile_owner_dispatch_status.py importing
    superboss-register.py, task-gateway.py's own _load_scripts_module(),
    etc.) -- never a second, re-typed copy of another script's logic."""
    path = os.path.join(SCRIPT_DIR, filename)
    spec = _ilu.spec_from_file_location(modname, path)
    mod = _ilu.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def _run(cmd, timeout=60, **kw):
    """Same (rc, stdout, stderr) shape as reconcile_owner_dispatch_status.py's
    own _run() -- kept independent (not imported) since this needs a longer
    default timeout for the dispatch-owner-task.sh call itself (which can
    run a real synchronous tier-3/4 claude_code_cli_headless invocation)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                            cwd=SCRIPT_DIR, **kw)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        return -1, (e.stdout or ""), f"TIMEOUT after {timeout}s: {e}"
    except Exception as e:
        return -1, "", str(e)


def _run_json(cmd, timeout=60):
    rc, out, err = _run(cmd, timeout=timeout)
    try:
        return rc, json.loads(out), err
    except (json.JSONDecodeError, ValueError):
        return rc, None, (err or out)


# ---------------------------------------------------------------------------
# Step 1/2: submit/classify + software-only deterministic gate
# ---------------------------------------------------------------------------

# Explicit allow-list only -- see module docstring step 2. Deliberately
# empty by default: no real capability_registry row has yet been reviewed
# and wired here as safe to auto-execute from this generic entrypoint.
# Adding an entry here is a real, reviewed decision (what shell/API call it
# maps to, and why it's safe to run unattended from arbitrary matched task
# text), never a generic "run capability['apis'][0]" executor.
SAFE_DETERMINISTIC_EXECUTORS = {}


def submit_classify(text, tier, session_id, attach=None):
    cmd = ["python3", "task-gateway.py", "submit", "--text", text,
           "--source", "owner", "--session-id", session_id, "--tier", str(tier)]
    if attach:
        cmd += ["--attach", attach]
    rc, out, err = _run(cmd, timeout=120)
    try:
        return json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return {"_submit_classify_error": err or out, "_rc": rc}


def check_deterministic_path(classify_result):
    """Real, honest check of step 1's own output -- never re-derives the
    decision independently (that would risk drifting from task-gateway.py
    submit's own single-source-of-truth capability_deterministic_path_available
    computation)."""
    available = bool(classify_result.get("capability_deterministic_path_available"))
    matches = [
        m for m in (classify_result.get("capability_matches") or [])
        if not m.get("ai_required") and m.get("apis")
    ]
    executable = [m for m in matches if m.get("capability_name") in SAFE_DETERMINISTIC_EXECUTORS]
    return {
        "capability_deterministic_path_available": available,
        "deterministic_capability_matches": matches,
        "auto_executable_matches": executable,
    }


# ---------------------------------------------------------------------------
# Step 3: dispatch
# ---------------------------------------------------------------------------

DISPATCHED_RE = re.compile(
    r"DISPATCHED: umr_id=(\S+) instruction_id=(\S+) work_item_id=(\S+) task_identity=(\S+)"
)
REGISTERED_ONLY_RE = re.compile(r"REGISTERED ONLY \(--no-relay\): umr_id=(\S+)")
RELAY_RE = re.compile(r"umr_id=(\S+)")


def build_tightened_prompt(objective, scope, success_criteria, expected_output,
                            known_context=None, complexity_tier="moderate"):
    """Assembles the labeled-field prompt shape tight_task_validation.py's
    own validate_tight_task()/parse_labeled_fields() validate against
    (## OBJECTIVE / ## SCOPE / ## SUCCESS_CRITERIA / ## EXPECTED_OUTPUT /
    ## KNOWN_CONTEXT / ## COMPLEXITY_TIER) -- built here, not left to the
    caller to hand-format, so every real dispatch this orchestrator makes
    is a real "validated tightened prompt" per this task's own SPEC step 3,
    not free text."""
    lines = [
        "## OBJECTIVE", objective.strip(), "",
        "## SCOPE", scope.strip(), "",
        "## SUCCESS_CRITERIA", success_criteria.strip(), "",
        "## EXPECTED_OUTPUT", expected_output.strip(), "",
    ]
    if known_context:
        lines += ["## KNOWN_CONTEXT", known_context.strip(), ""]
    lines += ["## COMPLEXITY_TIER", complexity_tier.strip()]
    return "\n".join(lines)


def dispatch_task(title, prompt, tier, medium, repo, attach=None, no_relay=False):
    cmd = [title, prompt, str(tier), medium, repo]
    if no_relay:
        cmd.append("--no-relay")
    if attach:
        cmd += ["--attach", attach]
    # tier 3/4 (claude_code_cli_headless) run synchronously inside this
    # call -- give it real headroom above tier_execution_config.json's own
    # 900s timeout_seconds so this wrapper is never the thing that kills a
    # real in-flight headless run.
    rc, out, err = _run(["./dispatch-owner-task.sh"] + cmd, timeout=1200)
    combined = out + "\n" + err

    result = {"rc": rc, "stdout": out, "stderr": err}
    m = DISPATCHED_RE.search(combined)
    if m:
        result.update({
            "umr_id": m.group(1), "instruction_id": m.group(2),
            "work_item_id": m.group(3), "task_identity": m.group(4),
            "outcome": "dispatched",
        })
        return result
    m = REGISTERED_ONLY_RE.search(combined)
    if m:
        result.update({"umr_id": m.group(1), "outcome": "registered_only"})
        return result
    if "REFUSED" in combined:
        m = RELAY_RE.search(combined)
        result.update({"umr_id": m.group(1) if m else None, "outcome": "refused"})
        return result
    if "REJECTED" in combined:
        m = RELAY_RE.search(combined)
        result.update({"umr_id": m.group(1) if m else None, "outcome": "rejected"})
        return result
    result["outcome"] = "unknown"
    return result


# ---------------------------------------------------------------------------
# Step 4: poll register until terminal
# ---------------------------------------------------------------------------

def query_umr(umr_id):
    rc, data, err = _run_json(["python3", "resource_governor.py", "--query-umr",
                                "--umr-id", umr_id, "--full"], timeout=60)
    if data is None:
        return None, err
    # resource_governor.py --query-umr prints {"count": N, "matches": [...]}
    # -- see its own --query-umr handler.
    rows = data.get("matches") if isinstance(data, dict) else data
    if not rows:
        return None, "no real umr_tasks row found"
    return rows[0], None


def poll_until_terminal(umr_id, poll_interval=DEFAULT_POLL_INTERVAL_SECONDS,
                         poll_timeout=DEFAULT_POLL_TIMEOUT_SECONDS,
                         query_fn=None, sleep_fn=None, now_fn=None):
    """Real polling loop -- injectable query_fn/sleep_fn/now_fn so this can
    be unit-tested deterministically (no real sleep, no real subprocess)
    per this task's own SPEC requirement for a regression test covering
    "the polling-to-terminal-state ... logic". Returns
    {"row": <last row or None>, "timed_out": bool, "samples": [...],
    "elapsed_seconds": float}."""
    query_fn = query_fn or (lambda uid: query_umr(uid))
    sleep_fn = sleep_fn or time.sleep
    now_fn = now_fn or time.monotonic

    start = now_fn()
    samples = []
    while True:
        row, err = query_fn(umr_id)
        elapsed = now_fn() - start
        status = row.get("status") if row else None
        samples.append({"elapsed_seconds": elapsed, "status": status, "error": err})
        if row and status not in UMR_ACTIVE_STATUSES:
            return {"row": row, "timed_out": False, "samples": samples, "elapsed_seconds": elapsed}
        if elapsed >= poll_timeout:
            return {"row": row, "timed_out": True, "samples": samples, "elapsed_seconds": elapsed}
        sleep_fn(poll_interval)


# ---------------------------------------------------------------------------
# Step 5: independent gh-based verification (reuses
# reconcile_owner_dispatch_status.py's own tested primitives directly)
# ---------------------------------------------------------------------------

def independent_verify(row, rodl_module):
    """Real, independent re-derivation of PR + audit state for a terminal
    umr_tasks row -- never trusts row['status'] alone. Mirrors
    reconcile_owner_dispatch_status.py's classify_row() lookup chain
    (unit_name -> task_id -> task.yaml -> repo/branch -> PR -> audit
    verdict), reusing its exact functions rather than re-deriving the same
    logic a second time. Falls back to the row's own outputs_json (repo/
    pr_number/commit_sha, written by mark-umr-terminal) when task.yaml
    lookup comes up empty (e.g. a tier-3/4 headless dispatch, which writes
    its own PR/repo straight into outputs_json rather than a task.yaml
    checkpoint file)."""
    unit_name = row.get("unit_name")
    task_id = rodl_module._task_dir_from_unit(unit_name)
    yml = rodl_module._read_task_yaml(task_id) or {}
    # _umr_row_to_dict() (superboss-register.py) already json.loads()s
    # outputs_json into a real dict when it's non-empty -- this only
    # re-parses if a caller ever hands this a raw string (e.g. a test
    # fixture), never double-parses a dict.
    outputs = row.get("outputs_json") or {}
    if isinstance(outputs, str):
        try:
            outputs = json.loads(outputs)
        except (json.JSONDecodeError, ValueError):
            outputs = {}

    repo = yml.get("repo") or outputs.get("repo")
    pr_number = outputs.get("pr_number")
    branch = yml.get("branch")

    evidence = {
        "umr_id": row.get("umr_id"), "task_id": task_id,
        "task_yaml_repo": yml.get("repo"), "task_yaml_branch": branch,
        "outputs_repo": outputs.get("repo"), "outputs_pr_number": outputs.get("pr_number"),
        "outputs_commit_sha": outputs.get("commit_sha"),
    }

    pr_cache = {}
    pr_match = None
    if repo and branch:
        for pr in rodl_module._fetch_prs(repo, pr_cache):
            if pr["headRefName"] == branch:
                pr_match = pr
                break
    if pr_match is None and repo and pr_number:
        for pr in rodl_module._fetch_prs(repo, pr_cache):
            if pr["number"] == pr_number:
                pr_match = pr
                break

    evidence["pr_match"] = pr_match
    audit = None
    if repo and pr_match:
        audit = rodl_module._audit_verdict(repo, pr_match["number"])
        evidence["audit"] = audit
    evidence["repo"] = repo
    return evidence


def fresh_audit_pass(evidence):
    audit = evidence.get("audit") or {}
    verdict = (audit.get("verdict") or {}).get("verdict") or ""
    return verdict.startswith("AUDIT: PASS") and audit.get("stale") is False


def fresh_audit_fail(evidence):
    audit = evidence.get("audit") or {}
    verdict = (audit.get("verdict") or {}).get("verdict") or ""
    return verdict.startswith("AUDIT: FAIL") and audit.get("stale") is not True


# ---------------------------------------------------------------------------
# Step 6: audit-fail retry
# ---------------------------------------------------------------------------

def should_retry_fix(evidence, retry_count, max_retries):
    """Pure decision function (no I/O) -- separated out so this task's own
    required regression test can cover "the ... audit-fail-retry logic"
    deterministically without any real gh/dispatch call."""
    if retry_count >= max_retries:
        return False
    return fresh_audit_fail(evidence)


def verify_with_retries(row, umr_id, rodl, tier, medium, repo, no_relay,
                         poll_interval, poll_timeout, max_fix_retries,
                         verify_fn=None, dispatch_fix_fn=None, poll_fn=None, query_fn=None):
    """Real step 5/6 loop, extracted to its own injectable function so this
    task's own SPEC-required regression test can cover "the ... audit-fail-
    retry logic" deterministically (fake verify_fn/dispatch_fix_fn/poll_fn/
    query_fn, no real gh/dispatch/subprocess calls) -- exercised by
    run_full_cycle() with the real functions as defaults. Returns
    {"verify_evidence": ..., "retries": int, "fix_history": [...], "row": ...}
    -- `row` is re-read after each real fix lands (same PR, new commit on
    the same branch), never re-derived from the fix dispatch's own umr_id."""
    verify_fn = verify_fn or independent_verify
    dispatch_fix_fn = dispatch_fix_fn or dispatch_audit_fix
    poll_fn = poll_fn or poll_until_terminal
    query_fn = query_fn or query_umr

    retries = 0
    verify_evidence = verify_fn(row, rodl)
    fix_history = []
    while should_retry_fix(verify_evidence, retries, max_fix_retries):
        fix_dispatch = dispatch_fix_fn(verify_evidence, tier, medium, repo, no_relay=no_relay)
        fix_history.append({"retry": retries, "dispatch": fix_dispatch})
        retries += 1
        if fix_dispatch["outcome"] not in ("dispatched", "registered_only"):
            break
        fix_umr_id = fix_dispatch["umr_id"]
        fix_poll = poll_fn(fix_umr_id, poll_interval=poll_interval, poll_timeout=poll_timeout)
        fix_history[-1]["poll"] = {k: v for k, v in fix_poll.items() if k != "samples"}
        if fix_poll["timed_out"]:
            break
        row, _ = query_fn(umr_id)  # re-read the ORIGINAL row (same PR, new commit on same branch)
        verify_evidence = verify_fn(row, rodl)
    return {"verify_evidence": verify_evidence, "retries": retries, "fix_history": fix_history, "row": row}


def dispatch_audit_fix(evidence, tier, medium, repo, no_relay=False):
    audit = evidence.get("audit") or {}
    verdict = audit.get("verdict") or {}
    pr = evidence.get("pr_match") or {}
    finding = verdict.get("verdict", "AUDIT: FAIL")
    title = f"Fix cited AUDIT:FAIL on PR #{pr.get('number')} ({evidence.get('repo')})"
    prompt = build_tightened_prompt(
        objective=(
            f"Fix the real, specific finding an independent audit posted on PR #{pr.get('number')} "
            f"({evidence.get('repo')}, branch {pr.get('headRefName')})."
        ),
        scope=f"Only PR #{pr.get('number')} in {evidence.get('repo')} -- push a real commit to the same branch.",
        success_criteria=(
            "Read the real full comment thread first: "
            f"gh pr view {pr.get('number')} --repo {GH_ORG}/{evidence.get('repo')} --comments -- "
            f"then fix the real cited issue. Verify with: git -C . log -1 --format=%H"
        ),
        expected_output="A real commit pushed to the same PR branch addressing the cited finding.",
        known_context=(
            f"Most recent audit verdict on this PR: {finding!r} (createdAt={verdict.get('createdAt')}). "
            "Do not fabricate completion."
        ),
        complexity_tier="moderate",
    )
    return dispatch_task(title, prompt, tier, medium, repo, no_relay=no_relay)


# ---------------------------------------------------------------------------
# Step 7: merge + re-verify
# ---------------------------------------------------------------------------

def merge_and_reverify(evidence):
    pr = evidence.get("pr_match") or {}
    repo = evidence.get("repo")
    number = pr.get("number")
    if not (repo and number):
        return {"merged": False, "reason": "no real repo/PR number to merge"}

    rc, out, err = _run(
        ["gh", "pr", "merge", str(number), "--repo", f"{GH_ORG}/{repo}", "--merge"],
        timeout=60,
    )
    merge_call = {"rc": rc, "stdout": out, "stderr": err}

    rc2, view, err2 = _run_json(
        ["gh", "pr", "view", str(number), "--repo", f"{GH_ORG}/{repo}",
         "--json", "state,mergedAt,mergeCommit,statusCheckRollup"],
        timeout=30,
    )
    merged = bool(view and view.get("state") == "MERGED" and view.get("mergedAt"))
    return {
        "merged": merged, "merge_call": merge_call,
        "post_merge_view": view, "post_merge_view_error": err2,
    }


def checks_evidence(view):
    rollup = (view or {}).get("statusCheckRollup") or []
    if not rollup:
        return {"tested": False, "reason": "no real CI status checks configured/found for this PR", "rollup": []}
    bad = [c for c in rollup
           if c.get("conclusion") not in ("SUCCESS", "NEUTRAL", "SKIPPED", None)
           and c.get("state") not in ("SUCCESS",)]
    return {"tested": not bad, "rollup": rollup, "failing": bad}


# ---------------------------------------------------------------------------
# Step 8: record the real 6-boolean status
# ---------------------------------------------------------------------------

def compute_six_columns(umr_row, verify_evidence, merge_result, checks_result):
    outputs = umr_row.get("outputs_json") or {}
    if isinstance(outputs, str):
        try:
            outputs = json.loads(outputs)
        except (json.JSONDecodeError, ValueError):
            outputs = {}

    completed = umr_row.get("status") in ("completed", "completed_unmerged") and bool(
        outputs.get("commit_sha") or (verify_evidence.get("pr_match") or {}).get("number"))
    tested = bool(checks_result.get("tested"))
    audited = fresh_audit_pass(verify_evidence)
    integrated = bool(merge_result.get("merged"))
    working = bool(merge_result.get("post_merge_view", {}) and integrated)
    certified = all([completed, tested, audited, integrated, working])

    return {
        "COMPLETED": {"value": completed, "evidence": {
            "register_status": umr_row.get("status"), "commit_sha": outputs.get("commit_sha"),
            "pr_number": (verify_evidence.get("pr_match") or {}).get("number")}},
        "TESTED": {"value": tested, "evidence": checks_result},
        "AUDITED": {"value": audited, "evidence": verify_evidence.get("audit")},
        "INTEGRATED": {"value": integrated, "evidence": merge_result.get("post_merge_view")},
        "WORKING": {"value": working, "evidence": {
            "post_merge_state": (merge_result.get("post_merge_view") or {}).get("state"),
            "post_merge_merge_commit": (merge_result.get("post_merge_view") or {}).get("mergeCommit")}},
        "CERTIFIED": {"value": certified, "evidence": {
            "all_five_real_and_true": certified,
            "note": "no self-certification -- every column above is independently derived from live gh/register state, not the worker's own claim"}},
    }


def record_six_columns(umr_id, six_columns, sbr_module):
    conn = sbr_module._connect()
    existing_row = conn.execute(
        "SELECT metadata_json FROM umr_tasks WHERE umr_id=?", (umr_id,)
    ).fetchone()
    existing_metadata = {}
    if existing_row and existing_row["metadata_json"]:
        try:
            existing_metadata = json.loads(existing_row["metadata_json"])
        except (json.JSONDecodeError, ValueError):
            existing_metadata = {"_unparseable_prior_metadata_json": existing_row["metadata_json"]}
    merged_metadata = dict(existing_metadata)
    merged_metadata["pm_lifecycle_full_cycle"] = {
        "recorded_by": "pm_lifecycle.py",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "six_columns": six_columns,
    }
    with sbr_module._write_lock():
        sbr_module.update_umr_task(conn, umr_id, metadata=merged_metadata)
        conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_full_cycle(args):
    rodl = _load_module("reconcile_owner_dispatch_status.py", "pm_lifecycle_rodl")
    sbr = rodl._sbr  # reconcile_owner_dispatch_status.py already loaded superboss-register.py

    session_id = f"pm_lifecycle.py:{args.medium}:{os.getpid()}"
    report = {"title": args.title}

    # Step 1/2
    classify = submit_classify(args.text, args.tier, session_id, attach=args.attach)
    report["classify"] = classify
    det = check_deterministic_path(classify)
    report["deterministic_gate"] = det
    if det["auto_executable_matches"]:
        # Real, reviewed, allow-listed executor path -- SAFE_DETERMINISTIC_EXECUTORS
        # is empty by default (see its own comment), so this branch is
        # currently unreachable in production; kept real (not stubbed) so a
        # future reviewed addition works without touching this function.
        match = det["auto_executable_matches"][0]
        executor = SAFE_DETERMINISTIC_EXECUTORS[match["capability_name"]]
        exec_result = executor(classify)
        report["software_only_execution"] = exec_result
        report["dispatched_ai_worker"] = False
        report["certified"] = bool(exec_result.get("success"))
        print(json.dumps(report, indent=2, default=str))
        return 0

    if classify.get("duplicate_found"):
        report["refused"] = "duplicate_found -- see classify.duplicate_evidence"
        print(json.dumps(report, indent=2, default=str))
        return 1

    # Step 3
    prompt = build_tightened_prompt(
        args.objective or args.text, args.scope or f"Repo: {args.repo}",
        args.success_criteria or "Real regression tests pass; verification command documented in the PR.",
        args.expected_output or "A real committed, pushed change with an open PR.",
        known_context=args.known_context, complexity_tier=args.complexity_tier,
    )
    dispatch = dispatch_task(args.title, prompt, args.tier, args.medium, args.repo,
                              attach=args.attach, no_relay=args.no_relay)
    report["dispatch"] = dispatch
    report["dispatched_ai_worker"] = True
    if dispatch["outcome"] in ("refused", "rejected", "unknown"):
        report["certified"] = False
        print(json.dumps(report, indent=2, default=str))
        return 1

    umr_id = dispatch["umr_id"]

    # Step 4
    poll = poll_until_terminal(umr_id, poll_interval=args.poll_interval,
                                poll_timeout=args.poll_timeout)
    report["poll"] = {k: v for k, v in poll.items() if k != "samples"}
    report["poll"]["sample_count"] = len(poll["samples"])
    report["poll"]["last_sample"] = poll["samples"][-1] if poll["samples"] else None
    if poll["timed_out"]:
        report["certified"] = False
        print(json.dumps(report, indent=2, default=str))
        return 2

    row = poll["row"]

    # Step 5/6: verify, retry on fresh AUDIT:FAIL up to max_fix_retries
    retry_result = verify_with_retries(
        row, umr_id, rodl, args.tier, args.medium, args.repo, args.no_relay,
        args.poll_interval, args.poll_timeout, args.max_fix_retries,
    )
    verify_evidence = retry_result["verify_evidence"]
    row = retry_result["row"]
    report["verify_evidence"] = verify_evidence
    report["fix_retries"] = retry_result["fix_history"]

    if not fresh_audit_pass(verify_evidence):
        report["certified"] = False
        report["reason"] = (
            "no fresh AUDIT:PASS after real independent verification "
            f"(and {retry_result['retries']} real fix "
            f"retr{'y' if retry_result['retries'] == 1 else 'ies'}) -- "
            "surfacing to PM rather than looping forever."
        )
        print(json.dumps(report, indent=2, default=str))
        return 0

    pr = verify_evidence.get("pr_match") or {}
    if pr.get("state") != "OPEN":
        report["certified"] = False
        report["reason"] = f"real PR state is {pr.get('state')!r}, not OPEN -- nothing to merge."
        print(json.dumps(report, indent=2, default=str))
        return 0

    # Step 7
    merge_result = merge_and_reverify(verify_evidence)
    report["merge_result"] = merge_result
    checks_result = checks_evidence(merge_result.get("post_merge_view"))

    # Step 8
    six_columns = compute_six_columns(row, verify_evidence, merge_result, checks_result)
    report["six_columns"] = six_columns
    report["certified"] = six_columns["CERTIFIED"]["value"]
    if not args.dry_run:
        record_six_columns(umr_id, six_columns, sbr)
        report["recorded_to_register"] = True
    else:
        report["recorded_to_register"] = False

    print(json.dumps(report, indent=2, default=str))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the full submit->dispatch->poll->verify->merge->record lifecycle for one real task")
    p_run.add_argument("--title", required=True)
    p_run.add_argument("--text", required=True, help="full raw task text (classification input)")
    p_run.add_argument("--repo", default="compliance-tracker")
    p_run.add_argument("--tier", type=int, default=2)
    p_run.add_argument("--medium", default="claude_code_cli", choices=["claude_code_cli", "ssh_session"])
    p_run.add_argument("--attach", default=None)
    p_run.add_argument("--no-relay", action="store_true")
    p_run.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS)
    p_run.add_argument("--poll-timeout", type=int, default=DEFAULT_POLL_TIMEOUT_SECONDS)
    p_run.add_argument("--max-fix-retries", type=int, default=DEFAULT_MAX_FIX_RETRIES)
    p_run.add_argument("--dry-run", action="store_true", help="do everything except the final register write")
    p_run.add_argument("--objective", default=None)
    p_run.add_argument("--scope", default=None)
    p_run.add_argument("--success-criteria", default=None)
    p_run.add_argument("--expected-output", default=None)
    p_run.add_argument("--known-context", default=None)
    p_run.add_argument("--complexity-tier", default="moderate")
    p_run.set_defaults(func=run_full_cycle)

    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    try:
        sys.exit(args.func(args))
    except Exception as e:
        print(json.dumps({"internal_error": str(e)}, indent=2), file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
