#!/usr/bin/env python3
"""status-remediation-tick.py -- task-20260726-210339 consolidation of
veridian_status_monitor.py + veridian_remediation_dispatcher.py --apply. Same
chained relationship the live crontab already ran these two scripts under
(`veridian_status_monitor.py && veridian_remediation_dispatcher.py --apply`),
now one process instead of two subprocess invocations: scans real status, then
classifies + remediates against that SAME in-memory scan, with one atomic
ai-os/LIVE_STATUS_2026-07-26.yaml write at the end instead of the monitor
writing it once and the dispatcher immediately re-reading + rewriting it.

The one real behavior change: scan_phases_ready() now reads
ai-os/PHASE_READY_CACHE.json (written every tick by phase-continuation-tick.py)
instead of re-running `auto_phase_continuation.py --dry-run` as a full
subprocess. That old re-run duplicated phase-continuation-tick.py's ENTIRE
plan-scan + per-phase `gh pr list --head worker/<id> --state merged` +
`git show master:...` cross-reference work, every single 10-minute
status-remediation tick, purely to populate this one read-only status field.

Everything else is unchanged from the two original scripts: the task.yaml
scan, the PR scan (audit-fail + merge-conflict), and -- per this task's own
CONSTRAINTS ("do not change veridian_remediation_dispatcher.py's
mechanical-fix behavior") -- remediation classification and both mechanical
paths (ci_timing_race -> `gh run rerun --failed`, transient_merge_retry ->
`gh pr merge --merge`) are copied verbatim, including their real
justification for staying 2 narrow direct `gh` calls instead of spawning a
full AI worker (see classify_audit_fail's docstring and the module docstring
below, both preserved word for word). This script has no worker-spawn call
site of its own, so it never imports dispatch_core.acquire_dispatch_lock() --
only record_tick(), for the same per-script wiring_registry row
dispatch-tick.py and phase-continuation-tick.py also write.

--- original veridian_remediation_dispatcher.py module docstring, unchanged ---

Real, cron-driven remediation dispatcher. Closes the second gap named in this
task's own SPEC: today a human/AI has to happen to notice a rejected/blocked PR
and manually dispatch a fix. This reads the real findings this script's own
monitor half just scanned (never re-scans a second time -- that would
duplicate the monitor's own gh calls) and, for each one, drafts a real
corrective task prompt in the same OBJECTIVE/SCOPE/KNOWN_CONTEXT/
SUCCESS_CRITERIA/EXPECTED_OUTPUT/CONSTRAINTS/COMPLEXITY_TIER format
tight_task_validation.py requires, then either:

  (a) MECHANICAL, auto-applied directly -- only the two narrow, explicitly-named
      classes this task's CONSTRAINTS permit, each reusing a real fix mechanism
      already proven live on this server, not a new invented one:
        - ci_timing_race: the audit-check CI job ran and failed BEFORE this
          repo's own AUDIT: PASS/FAIL comment existed (a guaranteed race, not a
          flake -- same root cause supervisor-entrypoint.sh's
          AUDIT-CHECK-RERUN-BLOCK already fixes for its own tier1-merge path).
          Fix: `gh run rerun <id> --failed`, same command that block already runs.
        - transient_merge_retry: a task was already tier1 Superboss-approved but
          the merge attempt itself failed, AND a fresh mergeStateStatus check
          right now shows CLEAN (not DIRTY) -- i.e. nothing is structurally
          wrong, the prior failure was transient. Fix: retry `gh pr merge
          --merge`, same call supervisor-entrypoint.sh's own merge block makes,
          polling mergeStateStatus first exactly like that block does.
      This script executes these two directly (subprocess gh calls) rather than
      routing them through task-gateway.py's AI-worker pipeline: spinning a full
      Claude Code worker session to run one `gh` command would contradict the
      Owner's own "audit run by software, not AI" mandate and everywhere else in
      this codebase mechanical fixes are applied as direct software actions, not
      AI-authored tasks (see audit_pipeline_security.py, backfill_phase_self_report.py).
      A corrective-task-prompt record is still drafted and filed for audit-trail
      parity with path (b) below -- it documents what was done and why, it is not
      something left for anyone to dispatch.

  (b) JUDGMENT-NEEDED, drafted only -- everything else: a genuine AUDIT: FAIL
      finding (the CI failure, if any, happened AFTER the real audit verdict
      existed, so it is a real correlated failure, not a race) or a real,
      non-transient merge conflict (mergeStateStatus DIRTY -- content actually
      conflicts, no software here can safely resolve that). The drafted prompt
      is written to ai-os/pending_remediation/<id>.md and flagged in the status
      artifact as "drafted, needs assistant review before dispatch". This script
      NEVER merges a PR, reruns CI, or dispatches a task for these -- per this
      task's own CONSTRAINTS, only real judgment (this assistant or the Owner)
      may act on them.

Mechanical actions are gated behind --apply (default off = dry-run: prints what
WOULD run, calls nothing real). The proposed cron entry passes --apply (see this
task's PR body). Judgment-path drafting never touches an external system (only
writes a local file) so it always runs for real, --apply or not.
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys

import yaml

import dispatch_core

AI_OS = dispatch_core.AI_OS
TASKS_DIR = dispatch_core.TASKS_DIR
OUTPUT_PATH = os.environ.get("VERIDIAN_LIVE_STATUS_PATH", f"{AI_OS}/LIVE_STATUS_2026-07-26.yaml")


def _load_generate_wiring_registry():
    """Lazy, in-process import of generate_wiring_registry.py -- same importlib
    pattern dispatch_core._superboss_register() already uses to load
    superboss-register.py, reused here so this tick can call the real generator
    in-process instead of a subprocess call or a new standalone cron entry (see
    this task's -- task-20260727-025248 -- own CONSTRAINTS: reuse an existing
    consolidated tick script for scheduling, do not add a new one)."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "generate_wiring_registry", os.path.join(script_dir, "generate_wiring_registry.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def refresh_wiring_registry():
    """Best-effort wiring_registry/knowledge_engine refresh, run once per tick
    (task-20260727-025248: makes the index "auto-updating... instead of a stale
    snapshot" per that task's own OBJECTIVE). A real full generate() run costs
    ~2s against the current ~7700-row live DB -- cheap enough to run on every
    10-minute tick with no throttling needed. Failure here is printed and
    swallowed, same "fails open, never load-bearing for the tick it wraps"
    principle dispatch_core.record_tick() already applies to its own
    wiring_registry write -- a broken doc/engine catalog read must never block
    the real remediation scan/dispatch this script exists to run."""
    try:
        gwr = _load_generate_wiring_registry()
        summary = gwr.generate()
        return {"ok": True, "entity_count": summary.get("entity_count")}
    except Exception as e:
        print(f"WARNING: wiring_registry refresh failed (non-fatal): {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}


PHASE_READY_CACHE_PATH = os.environ.get("VERIDIAN_PHASE_READY_CACHE", f"{AI_OS}/PHASE_READY_CACHE.json")
PENDING_DIR = os.environ.get("VERIDIAN_PENDING_REMEDIATION_DIR", f"{AI_OS}/pending_remediation")
REMEDIATION_LOG = os.environ.get("VERIDIAN_REMEDIATION_LOG", f"{AI_OS}/logs/remediation-dispatcher.jsonl")

GH_OWNER = "FChecklist"
TRACKED_REPOS = ["claude-control", "compliance-tracker", "projexa"]

# A "live" snapshot means recently-relevant, not the full multi-week backlog --
# real data confirms 244 of 405 real task.yaml files are status=blocked, the vast
# majority weeks-old and already superseded by retries. Only tasks blocked within
# this window are surfaced as actionable; older ones are real history, not live status.
BLOCKED_LOOKBACK_HOURS = 24

PR_URL_RE = re.compile(r"(https://github\.com/\S+/pull/\d+)")
AUDIT_CHECK_NAMES = {"audit-check", "mandatory audit check", "mandatory-audit-check"}
TRANSIENT_MERGE_NOTE_RE = re.compile(r"Superboss-approved, but the merge itself FAILED")

# The real race (supervisor-entrypoint.sh's AUDIT-CHECK-RERUN-BLOCK docstring, PR
# #560 incident) is a same-review-cycle race: `gh pr create` fires the check the
# instant it runs, and the AUDIT comment posts moments later in the same script
# invocation -- a gap of seconds, never days. A check that merely predates a much
# later, independent AUDIT comment (e.g. a stale run from days ago, re-reviewed on
# a later date with no new push) is NOT a race, it is unrelated data -- confirmed
# against real live data (compliance-tracker PR #484: a 2-day-old check incorrectly
# matched a later re-review before this threshold was added; PR #410: a genuine
# 21-second gap, correctly still classifies as a race with this threshold).
RACE_MAX_GAP_SECONDS = 15 * 60


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def human_elapsed(delta):
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        total_seconds = 0
    hours, rem = divmod(total_seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def run(cmd, timeout=30):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return 124, "", str(e)
    except FileNotFoundError as e:
        return 127, "", str(e)


def run_json(cmd, timeout=30):
    code, out, err = run(cmd, timeout=timeout)
    if code != 0:
        return None, f"exit {code}: {err.strip()[:300]}"
    try:
        return json.loads(out), None
    except json.JSONDecodeError as e:
        return None, f"bad json: {e}"


# ---------------------------------------------------------------------------
# task.yaml scan (was veridian_status_monitor.py's scan_tasks, unchanged)
# ---------------------------------------------------------------------------

def scan_tasks(errors):
    in_progress = []
    blocked_recent = []
    if not os.path.isdir(TASKS_DIR):
        errors.append(f"TASKS_DIR missing: {TASKS_DIR}")
        return in_progress, blocked_recent

    cutoff = now_utc() - datetime.timedelta(hours=BLOCKED_LOOKBACK_HOURS)

    for entry in sorted(os.listdir(TASKS_DIR)):
        task_yaml_path = os.path.join(TASKS_DIR, entry, "task.yaml")
        if not os.path.isfile(task_yaml_path):
            continue
        try:
            task = yaml.safe_load(open(task_yaml_path)) or {}
        except Exception as e:
            errors.append(f"unreadable task.yaml {entry}: {e}")
            continue

        status = task.get("status")
        task_id = task.get("id") or entry
        checkpoints = task.get("checkpoints") or []
        last_checkpoint = checkpoints[-1] if checkpoints else {}

        if status == "in_progress":
            created_at = parse_ts(task.get("created_at"))
            started_at = created_at or parse_ts(task.get("last_checkpoint_at"))
            elapsed = (now_utc() - started_at) if started_at else None
            in_progress.append({
                "task_id": task_id,
                "title": task.get("title"),
                "repo": task.get("repo"),
                "created_at": task.get("created_at"),
                "elapsed_seconds": int(elapsed.total_seconds()) if elapsed else None,
                "elapsed_human": human_elapsed(elapsed) if elapsed else "unknown",
                "workspace": task.get("workspace"),
            })

        elif status == "blocked":
            last_ts = parse_ts(task.get("last_checkpoint_at"))
            if last_ts is None or last_ts < cutoff:
                continue
            note = last_checkpoint.get("note") or task.get("status_reason") or ""
            pr_match = PR_URL_RE.search(note)
            review_verdict = None
            review_summary = None
            review_issues = []
            review_path = os.path.join(TASKS_DIR, entry, "review.json")
            if os.path.isfile(review_path):
                try:
                    review = json.load(open(review_path))
                    review_verdict = review.get("verdict")
                    review_summary = review.get("summary")
                    review_issues = review.get("issues") or []
                except Exception as e:
                    errors.append(f"unreadable review.json {entry}: {e}")

            blocked_recent.append({
                "task_id": task_id,
                "repo": task.get("repo"),
                "blocked_since": task.get("last_checkpoint_at"),
                "reason": note,
                "pr_url": pr_match.group(1) if pr_match else None,
                "review_verdict": review_verdict,
                "review_summary": review_summary,
                "review_issues": review_issues,
            })

    return in_progress, blocked_recent


# ---------------------------------------------------------------------------
# PR scan (audit-fail + merge-conflict) -- unchanged
# ---------------------------------------------------------------------------

def scan_prs(errors):
    audit_fail_unfixed = []
    merge_conflict = []

    for repo in TRACKED_REPOS:
        prs, err = run_json([
            "gh", "pr", "list", "--repo", f"{GH_OWNER}/{repo}", "--state", "open",
            "--json", "number,title,url,mergeStateStatus,updatedAt,headRefOid,headRefName",
        ])
        if prs is None:
            errors.append(f"gh pr list failed for {repo}: {err}")
            continue

        for pr in prs:
            number = pr["number"]
            url = pr["url"]

            if pr.get("mergeStateStatus") == "DIRTY":
                merge_conflict.append({
                    "repo": repo,
                    "number": number,
                    "url": url,
                    "title": pr.get("title"),
                    "merge_state_status": "DIRTY",
                    "updated_at": pr.get("updatedAt"),
                    "head_ref_name": pr.get("headRefName"),
                    "head_ref_oid": pr.get("headRefOid"),
                })

            comments, cerr = run_json([
                "gh", "api", f"repos/{GH_OWNER}/{repo}/issues/{number}/comments",
                "--jq", "[.[] | {created_at, body}]",
            ])
            if comments is None:
                errors.append(f"gh api comments failed for {repo}#{number}: {cerr}")
                continue

            audit_comments = [c for c in comments if (c.get("body") or "").startswith("AUDIT:")]
            if not audit_comments:
                continue
            audit_comments.sort(key=lambda c: c.get("created_at") or "")
            latest_audit = audit_comments[-1]
            if not (latest_audit.get("body") or "").startswith("AUDIT: FAIL"):
                continue  # most recent structured verdict is PASS -- not currently failing

            fail_ts = parse_ts(latest_audit.get("created_at"))

            commits_info, comerr = run_json([
                "gh", "pr", "view", str(number), "--repo", f"{GH_OWNER}/{repo}",
                "--json", "commits",
            ])
            last_commit_ts = None
            if commits_info and commits_info.get("commits"):
                last_commit_ts = parse_ts(commits_info["commits"][-1].get("committedDate"))
            elif comerr:
                errors.append(f"gh pr view commits failed for {repo}#{number}: {comerr}")

            corrective_commit_since = bool(
                last_commit_ts and fail_ts and last_commit_ts > fail_ts
            )
            if corrective_commit_since:
                continue

            checks, cherr = run_json([
                "gh", "pr", "view", str(number), "--repo", f"{GH_OWNER}/{repo}",
                "--json", "statusCheckRollup",
            ])
            check_runs = (checks or {}).get("statusCheckRollup") or []

            audit_fail_unfixed.append({
                "repo": repo,
                "number": number,
                "url": url,
                "title": pr.get("title"),
                "head_ref_name": pr.get("headRefName"),
                "head_ref_oid": pr.get("headRefOid"),
                "audit_fail_comment_at": latest_audit.get("created_at"),
                "audit_fail_excerpt": (latest_audit.get("body") or "")[:1200],
                "last_commit_at": commits_info["commits"][-1].get("committedDate")
                if commits_info and commits_info.get("commits") else None,
                "check_runs": [
                    {"name": c.get("name"), "conclusion": c.get("conclusion"),
                     "startedAt": c.get("startedAt"), "completedAt": c.get("completedAt")}
                    for c in check_runs if c.get("name")
                ],
            })

    return audit_fail_unfixed, merge_conflict


# ---------------------------------------------------------------------------
# phases-ready-to-advance -- reads phase-continuation-tick.py's cache instead
# of re-running its full discovery. This is the one real behavior change from
# the original two scripts (see module docstring above).
# ---------------------------------------------------------------------------

def scan_phases_ready(errors):
    if not os.path.isfile(PHASE_READY_CACHE_PATH):
        errors.append(f"PHASE_READY_CACHE.json missing at {PHASE_READY_CACHE_PATH} -- "
                       "phase-continuation-tick.py has not run yet this deployment")
        return []
    try:
        with open(PHASE_READY_CACHE_PATH) as f:
            cache = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        errors.append(f"PHASE_READY_CACHE.json unreadable: {e}")
        return []
    return cache.get("phases_ready_to_advance") or []


# ---------------------------------------------------------------------------
# Remediation: classification (pure functions, unchanged from
# veridian_remediation_dispatcher.py)
# ---------------------------------------------------------------------------

def log_action(record):
    os.makedirs(os.path.dirname(REMEDIATION_LOG), exist_ok=True)
    record = dict(record)
    record["ts"] = now_utc().isoformat()
    with open(REMEDIATION_LOG, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def classify_audit_fail(finding):
    """finding: one entry from LIVE_STATUS's prs_audit_fail_unfixed."""
    fail_ts = parse_ts(finding.get("audit_fail_comment_at"))
    for check in finding.get("check_runs") or []:
        name = (check.get("name") or "").strip().lower()
        if name not in AUDIT_CHECK_NAMES:
            continue
        if check.get("conclusion") != "FAILURE":
            continue
        started = parse_ts(check.get("startedAt"))
        if started and fail_ts:
            gap_seconds = (fail_ts - started).total_seconds()
            if 0 <= gap_seconds <= RACE_MAX_GAP_SECONDS:
                return {
                    "type": "mechanical",
                    "class": "ci_timing_race",
                    "reason": f"{check.get('name')} started {check.get('startedAt')}, only {int(gap_seconds)}s "
                              f"before this repo's own AUDIT verdict comment "
                              f"({finding.get('audit_fail_comment_at')}) existed -- same-review-cycle "
                              f"race, matching supervisor-entrypoint.sh's own AUDIT-CHECK-RERUN-BLOCK "
                              f"incident pattern (PR #560).",
                    "check_name": check.get("name"),
                }
    return {
        "type": "judgment_needed",
        "class": "genuine_audit_fail",
        "reason": "No audit-check run started before the AUDIT verdict comment existed -- this is a "
                   "real, correlated review finding, not a CI-timing race.",
    }


def classify_merge_conflict(finding):
    """finding: one entry from LIVE_STATUS's prs_merge_conflict. Always judgment --
    mergeStateStatus DIRTY means real content conflict, no proven-safe mechanical fix exists."""
    return {
        "type": "judgment_needed",
        "class": "real_merge_conflict",
        "reason": "mergeStateStatus=DIRTY: a real content conflict against the base branch, not a "
                  "transient state -- requires resolving actual conflicting changes, which is a "
                  "judgment call this dispatcher must not make.",
    }


def classify_transient_merge(finding, current_merge_state_status):
    """finding: one entry from LIVE_STATUS's tasks_blocked_recent whose reason note
    matches TRANSIENT_MERGE_NOTE_RE. current_merge_state_status: a freshly re-fetched
    (not cached) `gh pr view --json mergeStateStatus` result for the referenced PR."""
    if current_merge_state_status == "DIRTY":
        return {
            "type": "judgment_needed",
            "class": "merge_failed_real_conflict",
            "reason": "Checkpoint note says a Superboss-approved merge attempt failed, but a fresh "
                      "check now shows mergeStateStatus=DIRTY -- a real conflict exists, not a "
                      "transient failure; retrying the merge would not succeed and is not attempted.",
        }
    return {
        "type": "mechanical",
        "class": "transient_merge_retry",
        "reason": f"Checkpoint note confirms this PR was already tier1 Superboss-approved but the "
                  f"merge attempt itself failed; a fresh check now shows mergeStateStatus="
                  f"{current_merge_state_status} (not DIRTY) -- nothing structurally wrong, the prior "
                  f"failure was transient (GitHub API/network hiccup). Safe to retry the same merge "
                  f"call supervisor-entrypoint.sh's own tier1 merge block already makes.",
    }


# ---------------------------------------------------------------------------
# Corrective task prompt drafting (same format for both paths, unchanged)
# ---------------------------------------------------------------------------

def draft_prompt(finding_id, objective, scope, known_context, success_criteria, expected_output,
                  constraints, complexity_tier):
    return f"""## OBJECTIVE
{objective}

## SCOPE
{scope}

## KNOWN_CONTEXT
{known_context}

## SUCCESS_CRITERIA
{success_criteria}

## EXPECTED_OUTPUT
{expected_output}

## CONSTRAINTS
{constraints}

## COMPLEXITY_TIER
{complexity_tier}
"""


def draft_for_audit_fail(finding, classification):
    repo = finding["repo"]
    number = finding["number"]
    url = finding["url"]
    return draft_prompt(
        finding_id=f"audit-fail-{repo}-{number}",
        objective=f"Fix the real AUDIT: FAIL finding on open PR {url} (compliance-tracker/claude-control/"
                  f"projexa worker-review pipeline) so a re-review can pass, without touching unrelated code.",
        scope=f"Read the real AUDIT: FAIL comment body on {url} in full (`gh api "
              f"repos/{GH_OWNER}/{repo}/issues/{number}/comments`) and the real diff "
              f"(`gh pr diff {number} --repo {GH_OWNER}/{repo}`). Address every issue it lists with a "
              f"real code change on the existing PR branch `{finding.get('head_ref_name')}`, not a new "
              f"branch. Do not re-argue the finding -- if it is factually wrong, say so explicitly in "
              f"the corrective commit message with evidence, do not silently ignore it.",
        known_context=f"Raised automatically by scripts/status-remediation-tick.py "
                      f"(classification: {classification['class']}, reason: {classification['reason']}). "
                      f"Full finding excerpt:\n{finding.get('audit_fail_excerpt', '')[:1500]}",
        success_criteria=f"- `gh pr view {number} --repo {GH_OWNER}/{repo} --json state` still shows OPEN "
                         f"and mergeable after the fix\n"
                         f"- A new commit exists on the branch after {finding.get('audit_fail_comment_at')} "
                         f"that addresses each listed issue\n"
                         f"- The next AUDIT comment on this PR is AUDIT: PASS, not AUDIT: FAIL",
        expected_output=f"A real corrective commit pushed to {url}'s branch, and a checkpoint note "
                        f"summarizing which of the listed issues were fixed vs disputed (with evidence).",
        constraints="Do not merge this PR yourself. Do not modify audit/review scripts to make the "
                   "finding pass without a real code fix. Stay within the files the original PR touched "
                   "plus whatever files the finding requires changing.",
        complexity_tier="judgment",
    )


def draft_for_merge_conflict(finding):
    repo = finding["repo"]
    number = finding["number"]
    url = finding["url"]
    return draft_prompt(
        finding_id=f"merge-conflict-{repo}-{number}",
        objective=f"Resolve the real merge conflict blocking open PR {url} against its base branch so "
                  f"it can merge cleanly, preserving both sides' real intent.",
        scope=f"Fetch the real base branch, run `git merge-base` / rebase the PR branch "
              f"`{finding.get('head_ref_name')}` onto the current base, and resolve every real "
              f"conflicting hunk by reading both sides' actual changes (not by blindly picking one "
              f"side). Push the resolved branch back to the same PR.",
        known_context=f"Raised automatically by scripts/status-remediation-tick.py "
                      f"(classification: real_merge_conflict, mergeStateStatus=DIRTY as of the "
                      f"triggering LIVE_STATUS run). This is a real content conflict, not a transient "
                      f"GitHub state -- confirmed by classify_merge_conflict() before this prompt was drafted.",
        success_criteria=f"- `gh pr view {number} --repo {GH_OWNER}/{repo} --json mergeStateStatus` "
                         f"returns CLEAN (not DIRTY) after the fix\n"
                         f"- No test or check that was passing before the resolve now fails",
        expected_output=f"A real conflict-resolution commit pushed to {url}'s branch, with a checkpoint "
                        f"note explaining how each conflicting hunk was resolved and why.",
        constraints="Do not merge this PR yourself once conflicts are resolved -- that is the normal "
                   "supervisor review path's job, not this corrective task's. Do not force-push over "
                   "the other side's real changes; every real change from both branches must be "
                   "preserved or explicitly, visibly dropped with a stated reason.",
        complexity_tier="judgment",
    )


# ---------------------------------------------------------------------------
# Mechanical actions (real subprocess gh calls, gated by --apply) -- unchanged.
# Deliberately NOT routed through dispatch_core -- neither of these spawns a
# worker (see module docstring); the shared concurrency cap this task adds is
# about worker/supervisor spawn paths, and this script has none.
# ---------------------------------------------------------------------------

def action_rerun_ci(finding, apply_):
    repo = finding["repo"]
    branch = finding.get("head_ref_name")
    run_json_result, err = run_json([
        "gh", "run", "list", "--repo", f"{GH_OWNER}/{repo}", "--branch", branch or "",
        "--json", "databaseId,status,conclusion,headSha,workflowName", "--limit", "10",
    ])
    if run_json_result is None:
        return {"applied": False, "error": f"gh run list failed: {err}"}

    target_run = None
    for r in run_json_result:
        if r.get("headSha") == finding.get("head_ref_oid") and r.get("status") == "completed" \
                and (r.get("workflowName") or "").strip().lower() in AUDIT_CHECK_NAMES:
            target_run = r
            break
    if not target_run:
        return {"applied": False, "error": "no matching completed audit-check run found for headSha"}

    if not apply_:
        return {"applied": False, "dry_run": True,
                "would_run": ["gh", "run", "rerun", str(target_run["databaseId"]),
                               "--repo", f"{GH_OWNER}/{repo}", "--failed"]}

    code, out, err = run(["gh", "run", "rerun", str(target_run["databaseId"]),
                           "--repo", f"{GH_OWNER}/{repo}", "--failed"])
    return {"applied": code == 0, "run_id": target_run["databaseId"], "stdout": out, "stderr": err}


def action_retry_merge(pr_url, repo, apply_):
    if not apply_:
        return {"applied": False, "dry_run": True,
                "would_run": ["gh", "pr", "merge", pr_url, "--merge"]}

    state, err = run_json(["gh", "pr", "view", pr_url, "--json", "mergeStateStatus"])
    if state is None or state.get("mergeStateStatus") in ("BLOCKED", "BEHIND"):
        return {"applied": False, "error": f"not ready to retry: {state or err}"}

    code, out, err = run(["gh", "pr", "merge", pr_url, "--merge"])
    final, ferr = run_json(["gh", "pr", "view", pr_url, "--json", "state,mergedAt"])
    merged = bool(final and final.get("state") == "MERGED" and final.get("mergedAt"))
    return {"applied": merged, "gh_merge_stdout": out, "gh_merge_stderr": err, "final_state": final}


# ---------------------------------------------------------------------------
# Pending-file writer (judgment path) -- unchanged
# ---------------------------------------------------------------------------

def write_pending(finding_id, prompt_text, classification, extra):
    os.makedirs(PENDING_DIR, exist_ok=True)
    path = os.path.join(PENDING_DIR, f"{finding_id}.md")
    header = (
        f"<!-- DRAFTED BY scripts/status-remediation-tick.py at {now_utc().isoformat()} -->\n"
        f"<!-- classification: {classification['class']} ({classification['type']}) -->\n"
        f"<!-- reason: {classification['reason']} -->\n"
        f"<!-- STATUS: drafted, needs assistant review before dispatch (not auto-dispatched) -->\n\n"
    )
    with open(path, "w") as f:
        f.write(header + prompt_text)
    return path


# ---------------------------------------------------------------------------
# Main remediation processing -- unchanged
# ---------------------------------------------------------------------------

def process(status_artifact, apply_):
    auto_dispatched = []
    drafted_pending = []

    for finding in status_artifact.get("prs_audit_fail_unfixed") or []:
        classification = classify_audit_fail(finding)
        finding_id = f"audit-fail-{finding['repo']}-{finding['number']}"
        prompt_text = draft_for_audit_fail(finding, classification)

        if classification["type"] == "mechanical":
            result = action_rerun_ci(finding, apply_)
            record = {"finding_id": finding_id, "class": classification["class"],
                       "reason": classification["reason"], "url": finding["url"], "action_result": result}
            log_action({"finding_id": finding_id, "path": "mechanical", **classification, "result": result})
            auto_dispatched.append(record)
        else:
            path = write_pending(finding_id, prompt_text, classification, finding)
            record = {"finding_id": finding_id, "class": classification["class"],
                       "reason": classification["reason"], "url": finding["url"], "pending_file": path}
            log_action({"finding_id": finding_id, "path": "judgment_needed", **classification, "pending_file": path})
            drafted_pending.append(record)

    for finding in status_artifact.get("prs_merge_conflict") or []:
        classification = classify_merge_conflict(finding)
        finding_id = f"merge-conflict-{finding['repo']}-{finding['number']}"
        prompt_text = draft_for_merge_conflict(finding)
        path = write_pending(finding_id, prompt_text, classification, finding)
        record = {"finding_id": finding_id, "class": classification["class"],
                   "reason": classification["reason"], "url": finding["url"], "pending_file": path}
        log_action({"finding_id": finding_id, "path": "judgment_needed", **classification, "pending_file": path})
        drafted_pending.append(record)

    for finding in status_artifact.get("tasks_blocked_recent") or []:
        note = finding.get("reason") or ""
        if not TRANSIENT_MERGE_NOTE_RE.search(note):
            continue
        pr_url = finding.get("pr_url")
        if not pr_url:
            continue
        state, err = run_json(["gh", "pr", "view", pr_url, "--json", "mergeStateStatus"])
        current_status = state.get("mergeStateStatus") if state else "UNKNOWN"
        classification = classify_transient_merge(finding, current_status)
        finding_id = f"merge-retry-{finding['task_id']}"

        if classification["type"] == "mechanical":
            result = action_retry_merge(pr_url, finding.get("repo"), apply_)
            record = {"finding_id": finding_id, "class": classification["class"],
                       "reason": classification["reason"], "url": pr_url, "action_result": result}
            log_action({"finding_id": finding_id, "path": "mechanical", **classification, "result": result})
            auto_dispatched.append(record)
        else:
            prompt_text = draft_prompt(
                finding_id=finding_id,
                objective=f"Resolve the real merge conflict now blocking {pr_url}, previously "
                          f"Superboss-approved but failed to merge.",
                scope=f"Same as any real merge-conflict corrective task: rebase/resolve the real "
                      f"conflicting content on {pr_url}, then push.",
                known_context=f"task {finding['task_id']} checkpoint note: {note}. Fresh check shows "
                              f"mergeStateStatus={current_status}.",
                success_criteria=f"- `gh pr view {pr_url} --json mergeStateStatus` returns CLEAN",
                expected_output="A real conflict-resolution commit pushed to the PR branch.",
                constraints="Do not merge this PR yourself once resolved.",
                complexity_tier="judgment",
            )
            path = write_pending(finding_id, prompt_text, classification, finding)
            record = {"finding_id": finding_id, "class": classification["class"],
                       "reason": classification["reason"], "url": pr_url, "pending_file": path}
            log_action({"finding_id": finding_id, "path": "judgment_needed", **classification, "pending_file": path})
            drafted_pending.append(record)

    return auto_dispatched, drafted_pending


def self_test():
    """Constructs 2 synthetic, clearly-labeled fixtures (no real gh mutations --
    classification is pure, and actions are called with apply_=False) to prove both
    the mechanical and judgment-needed classification paths, per this task's own
    SUCCESS_CRITERIA fallback ('construct one safe real test case ... then clean
    up the throwaway'). Also creates and deletes one real throwaway task.yaml so
    the transient_merge_retry path is exercised through the exact same
    tasks_blocked_recent shape the real cron run consumes, not a hand-shortcut."""
    print("=== SELF-TEST (synthetic fixtures, no real gh mutations) ===\n")

    synthetic_audit_fail = {
        "repo": "compliance-tracker", "number": 999999, "url": "https://github.com/FChecklist/compliance-tracker/pull/999999",
        "head_ref_name": "worker/synthetic-test-branch", "head_ref_oid": "deadbeef",
        "audit_fail_comment_at": "2026-07-26T05:00:00+00:00",
        "audit_fail_excerpt": "AUDIT: FAIL (synthetic -- constructed for self-test, not a real PR)",
        "check_runs": [{"name": "audit-check", "conclusion": "FAILURE",
                         "startedAt": "2026-07-26T04:59:00+00:00", "completedAt": "2026-07-26T04:59:10+00:00"}],
    }
    c1 = classify_audit_fail(synthetic_audit_fail)
    print("Fixture 1 (audit-check started 04:59:00, BEFORE the AUDIT comment at 05:00:00):")
    print(json.dumps(c1, indent=2))
    assert c1["type"] == "mechanical" and c1["class"] == "ci_timing_race"
    action1 = action_rerun_ci(synthetic_audit_fail, apply_=False)
    print("action_rerun_ci(apply_=False):", json.dumps(action1, indent=2))
    print()

    synthetic_audit_fail_2 = dict(synthetic_audit_fail)
    synthetic_audit_fail_2["check_runs"] = [{"name": "audit-check", "conclusion": "FAILURE",
                                              "startedAt": "2026-07-26T05:01:00+00:00", "completedAt": "2026-07-26T05:01:10+00:00"}]
    c2 = classify_audit_fail(synthetic_audit_fail_2)
    print("Fixture 2 (audit-check started 05:01:00, AFTER the AUDIT comment at 05:00:00):")
    print(json.dumps(c2, indent=2))
    assert c2["type"] == "judgment_needed" and c2["class"] == "genuine_audit_fail"
    print()

    throwaway_id = "task-TESTFIXTURE-mechanical-remediation-proof"
    throwaway_dir = os.path.join(TASKS_DIR, throwaway_id)
    os.makedirs(throwaway_dir, exist_ok=True)
    throwaway_yaml = f"""id: {throwaway_id}
title: SYNTHETIC test fixture -- constructed and deleted by status-remediation-tick.py --self-test
status: blocked
repo: compliance-tracker
branch: worker/{throwaway_id}
created_at: '{now_utc().isoformat()}'
last_checkpoint_at: '{now_utc().isoformat()}'
checkpoints:
- at: '{now_utc().isoformat()}'
  status: blocked
  note: "tier1, Superboss-approved, but the merge itself FAILED (gh pr view confirms state=OPEN, mergedAt=null; see supervisor.log) — needs manual attention, NOT actually merged: https://github.com/FChecklist/compliance-tracker/pull/999998"
"""
    with open(os.path.join(throwaway_dir, "task.yaml"), "w") as f:
        f.write(throwaway_yaml)
    try:
        task = yaml.safe_load(open(os.path.join(throwaway_dir, "task.yaml")))
        note = task["checkpoints"][-1]["note"]
        pr_match = re.search(r"(https://github\.com/\S+/pull/\d+)", note)
        blocked_finding = {"task_id": throwaway_id, "repo": "compliance-tracker",
                            "reason": note, "pr_url": pr_match.group(1)}
        print(f"Real throwaway task.yaml written: {throwaway_dir}/task.yaml")
        print("Parsed finding:", json.dumps(blocked_finding, indent=2))
        assert TRANSIENT_MERGE_NOTE_RE.search(blocked_finding["reason"])
        c3 = classify_transient_merge(blocked_finding, current_merge_state_status="CLEAN")
        print("classify_transient_merge(current_merge_state_status='CLEAN'):")
        print(json.dumps(c3, indent=2))
        assert c3["type"] == "mechanical" and c3["class"] == "transient_merge_retry"
        action3 = action_retry_merge(blocked_finding["pr_url"], blocked_finding["repo"], apply_=False)
        print("action_retry_merge(apply_=False):", json.dumps(action3, indent=2))

        c3b = classify_transient_merge(blocked_finding, current_merge_state_status="DIRTY")
        print("\nSame finding, but current_merge_state_status='DIRTY' (real conflict, not transient):")
        print(json.dumps(c3b, indent=2))
        assert c3b["type"] == "judgment_needed" and c3b["class"] == "merge_failed_real_conflict"
    finally:
        os.remove(os.path.join(throwaway_dir, "task.yaml"))
        os.rmdir(throwaway_dir)
        print(f"\nThrowaway cleaned up: {throwaway_dir} removed.")

    print("\n=== SELF-TEST PASSED: both mechanical and judgment_needed paths correctly classified ===")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                         help="Actually execute mechanical fixes (gh run rerun / gh pr merge retry). "
                              "Default is dry-run: classification + drafting happen for real, but "
                              "mechanical actions only print what they would do.")
    parser.add_argument("--self-test", action="store_true",
                         help="Run synthetic-fixture classification proof (no real gh mutations) and exit.")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    errors = []
    in_progress, blocked_recent = scan_tasks(errors)
    audit_fail_unfixed, merge_conflict = scan_prs(errors)
    phases_ready = scan_phases_ready(errors)

    artifact = {
        "generated_at": now_utc().isoformat(),
        "generator": "scripts/status-remediation-tick.py",
        "tracked_repos": [f"{GH_OWNER}/{r}" for r in TRACKED_REPOS],
        "blocked_lookback_hours": BLOCKED_LOOKBACK_HOURS,
        "tasks_in_progress": in_progress,
        "tasks_blocked_recent": blocked_recent,
        "prs_audit_fail_unfixed": audit_fail_unfixed,
        "prs_merge_conflict": merge_conflict,
        "phases_ready_to_advance": phases_ready,
        "scan_errors": errors,
    }

    auto_dispatched, drafted_pending = process(artifact, args.apply)
    artifact["remediation"] = {
        "last_run_at": now_utc().isoformat(),
        "apply_mode": args.apply,
        "auto_dispatched": auto_dispatched,
        "drafted_pending_review": drafted_pending,
    }

    tmp_path = OUTPUT_PATH + ".tmp"
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(tmp_path, "w") as f:
        yaml.safe_dump(artifact, f, sort_keys=False, default_flow_style=False, width=100)
    os.replace(tmp_path, OUTPUT_PATH)

    wiring_refresh = refresh_wiring_registry()

    dispatch_core.record_tick(
        "status-remediation-tick", status="ok", dispatched_this_tick=0,
        extra={
            "auto_dispatched_count": len(auto_dispatched),
            "drafted_pending_review_count": len(drafted_pending),
            "scan_errors": len(errors),
            "wiring_registry_refresh": wiring_refresh,
        },
    )

    summary = {
        "written_to": OUTPUT_PATH,
        "tasks_in_progress": len(in_progress),
        "tasks_blocked_recent": len(blocked_recent),
        "prs_audit_fail_unfixed": len(audit_fail_unfixed),
        "prs_merge_conflict": len(merge_conflict),
        "phases_ready_to_advance": len(phases_ready),
        "scan_errors": len(errors),
        "apply_mode": args.apply,
        "auto_dispatched_count": len(auto_dispatched),
        "drafted_pending_review_count": len(drafted_pending),
        "wiring_registry_refresh": wiring_refresh,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
