#!/usr/bin/env python3
"""Real, cron-driven remediation dispatcher. Closes the second gap named in this
task's own SPEC: today a human/AI has to happen to notice a rejected/blocked PR
and manually dispatch a fix. This reads the real findings
scripts/veridian_status_monitor.py already wrote to ai-os/LIVE_STATUS_2026-07-26.yaml
(never re-scans -- that would duplicate the monitor's own gh calls) and, for each
one, drafts a real corrective task prompt in the same OBJECTIVE/SCOPE/KNOWN_CONTEXT/
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
WOULD run, calls nothing real). The cron entry passes --apply. Judgment-path
drafting never touches an external system (only writes a local file) so it
always runs for real, --apply or not.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

AI_OS = "/opt/veridian/ai-os"
SCRIPTS = "/opt/veridian/scripts"
STATUS_PATH = f"{AI_OS}/LIVE_STATUS_2026-07-26.yaml"
PENDING_DIR = f"{AI_OS}/pending_remediation"
REMEDIATION_LOG = f"{AI_OS}/logs/remediation-dispatcher.jsonl"
TASKS_DIR = f"{AI_OS}/tasks"

GH_OWNER = "FChecklist"
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


def run(cmd, timeout=30):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return 124, "", str(e)


def run_json(cmd, timeout=30):
    code, out, err = run(cmd, timeout=timeout)
    if code != 0:
        return None, f"exit {code}: {err.strip()[:300]}"
    try:
        return json.loads(out), None
    except json.JSONDecodeError as e:
        return None, f"bad json: {e}"


def log_action(record):
    os.makedirs(os.path.dirname(REMEDIATION_LOG), exist_ok=True)
    record = dict(record)
    record["ts"] = now_utc().isoformat()
    with open(REMEDIATION_LOG, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


# ---------------------------------------------------------------------------
# Classification (pure functions -- no gh calls, no side effects; this is what
# the self-test below exercises directly to prove both paths without touching
# any real system).
# ---------------------------------------------------------------------------

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
# Corrective task prompt drafting (same format for both paths)
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
        known_context=f"Raised automatically by scripts/veridian_remediation_dispatcher.py "
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
        known_context=f"Raised automatically by scripts/veridian_remediation_dispatcher.py "
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
# Mechanical actions (real subprocess gh calls; gated by --apply)
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
# Pending-file writer (judgment path)
# ---------------------------------------------------------------------------

def write_pending(finding_id, prompt_text, classification, extra):
    os.makedirs(PENDING_DIR, exist_ok=True)
    path = os.path.join(PENDING_DIR, f"{finding_id}.md")
    header = (
        f"<!-- DRAFTED BY scripts/veridian_remediation_dispatcher.py at {now_utc().isoformat()} -->\n"
        f"<!-- classification: {classification['class']} ({classification['type']}) -->\n"
        f"<!-- reason: {classification['reason']} -->\n"
        f"<!-- STATUS: drafted, needs assistant review before dispatch (not auto-dispatched) -->\n\n"
    )
    with open(path, "w") as f:
        f.write(header + prompt_text)
    return path


# ---------------------------------------------------------------------------
# Main processing
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

    # --- Fixture 1: mechanical / ci_timing_race ---
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

    # --- Fixture 2: judgment_needed / genuine_audit_fail (using the real PR #562 shape) ---
    synthetic_audit_fail_2 = dict(synthetic_audit_fail)
    synthetic_audit_fail_2["check_runs"] = [{"name": "audit-check", "conclusion": "FAILURE",
                                              "startedAt": "2026-07-26T05:01:00+00:00", "completedAt": "2026-07-26T05:01:10+00:00"}]
    c2 = classify_audit_fail(synthetic_audit_fail_2)
    print("Fixture 2 (audit-check started 05:01:00, AFTER the AUDIT comment at 05:00:00):")
    print(json.dumps(c2, indent=2))
    assert c2["type"] == "judgment_needed" and c2["class"] == "genuine_audit_fail"
    print()

    # --- Fixture 3: mechanical / transient_merge_retry, via a REAL throwaway task.yaml ---
    throwaway_id = "task-TESTFIXTURE-mechanical-remediation-proof"
    throwaway_dir = os.path.join(TASKS_DIR, throwaway_id)
    os.makedirs(throwaway_dir, exist_ok=True)
    throwaway_yaml = f"""id: {throwaway_id}
title: SYNTHETIC test fixture -- constructed and deleted by veridian_remediation_dispatcher.py --self-test
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
        import yaml
        task = yaml.safe_load(open(os.path.join(throwaway_dir, "task.yaml")))
        note = task["checkpoints"][-1]["note"]
        pr_match = re.search(r"(https://github\.com/\S+/pull/\d+)", note)
        blocked_finding = {"task_id": throwaway_id, "repo": "compliance-tracker",
                            "reason": note, "pr_url": pr_match.group(1)}
        print(f"Real throwaway task.yaml written: {throwaway_dir}/task.yaml")
        print("Parsed finding:", json.dumps(blocked_finding, indent=2))
        assert TRANSIENT_MERGE_NOTE_RE.search(blocked_finding["reason"])
        # current_merge_state_status is set to CLEAN here to simulate "the transient
        # failure has since cleared" -- the real cron run fetches this fresh via gh;
        # PR #999998 does not exist so a real gh call is deliberately not made here.
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

    import yaml
    if not os.path.isfile(STATUS_PATH):
        print(json.dumps({"error": f"{STATUS_PATH} does not exist -- run veridian_status_monitor.py first"}))
        return 1
    status_artifact = yaml.safe_load(open(STATUS_PATH)) or {}

    auto_dispatched, drafted_pending = process(status_artifact, args.apply)

    status_artifact["remediation"] = {
        "last_run_at": now_utc().isoformat(),
        "apply_mode": args.apply,
        "auto_dispatched": auto_dispatched,
        "drafted_pending_review": drafted_pending,
    }
    tmp_path = STATUS_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        yaml.safe_dump(status_artifact, f, sort_keys=False, default_flow_style=False, width=100)
    os.replace(tmp_path, STATUS_PATH)

    print(json.dumps({
        "apply_mode": args.apply,
        "auto_dispatched_count": len(auto_dispatched),
        "drafted_pending_review_count": len(drafted_pending),
        "auto_dispatched": auto_dispatched,
        "drafted_pending_review": [{"finding_id": r["finding_id"], "class": r["class"],
                                     "pending_file": r["pending_file"]} for r in drafted_pending],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
