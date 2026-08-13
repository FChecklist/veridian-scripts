#!/usr/bin/env python3
"""
reconcile_stale_running_workers.py -- STEP 3 fix, task-20260807-052027-platform-integrity--
worker-units-exit-0 (UMR-20260807-020911-7f31).

One-time (but safely re-runnable / idempotent) sweep over the EXISTING backlog created by
the STEP 1 root-cause defect: umr_tasks rows at status='running', bound to a real
veridian-worker@<task_id>.service unit, whose unit is confirmed NOT ActiveState=active --
i.e. the worker already stopped (clean exit, crash, or exhausted retries) and, because of
the STEP 1 defect (now fixed prospectively by worker-exit-status-bridge.py's ExecStopPost
hook, wired into systemd/veridian-worker@.service), never wrote a terminal status back to
this row. See this task's own PROGRESS.md for the full root-cause writeup.

Deterministic, evidence-based, per real artifact -- never guesses, and NEVER trusts a
worker process's exit code as evidence of substantive completion (this task's own hard
SPEC constraint):

  1. Resolve the real task directory for this row -- outputs_json.new_task_id first (the
     exact, direct pointer the owner-dispatch pipeline itself records when it creates a
     task), falling back to task_identity as a literal TASKS_DIR directory name, falling
     back to a 'reconcile-umr-<umr id suffix>' directory-name match (the same fallback
     order resource_governor.py's own _task_yaml_for_umr_row() already uses for the
     analogous Stage-1 heartbeat backfill -- reused here for consistency, not reinvented).
  2. No task directory / no task.yaml found at all -> genuinely absent evidence -> real
     re-queue (`superboss-register.py reset-umr-to-queued`), never a guess at completed or
     failed.
  3. A real, referenced branch with a real, pushed commit -> ask `mark-umr-terminal`
     itself to verify (never our own git-ancestry guess): try --status completed first (a
     real commit-sha that IS an ancestor of origin/main, its own real, independent check);
     if refused specifically because the commit is real but not yet merged, retry
     --status completed_unmerged with the identical commit-sha. Both are gated by
     validate_umr_terminal_completion_evidence()'s own real check -- this script only ever
     supplies a candidate SHA (the branch's real remote tip via `git ls-remote`, not a
     guess), never asserts the outcome itself.
  4. No real commit evidence, and task.yaml's own last checkpoint status is a genuinely
     self-reported negative outcome (failed/blocked/cancelled/rejected_duplicate/
     superseded/not_needed) -> mark-umr-terminal --status failed.
  5. Anything else (task.yaml stuck at in_progress/pending with zero real commit
     evidence -- the worker was killed/crashed mid-work and nothing durable resulted, or a
     structured-evidence gate refused a real-looking commit for an unexpected reason) ->
     genuinely ambiguous -> real re-queue.

Duplicate-safety: re-queueing NEVER creates a new umr_tasks row -- it is
reset_umr_task_to_queued() applied to the SAME existing row (status='running' ->
'queued'), and 'queued' is itself one of UMR_ACTIVE_STATUSES, so
find_active_umr_by_identity()'s own existing dedup check continues to see this
task_identity as active and refuses a second concurrent dispatch for it, exactly like any
other real queued row -- no new mechanism needed, this falls out of the existing dedup
check for free.

Every DB write goes through the existing superboss-register.py CLI (mark-umr-terminal /
reset-umr-to-queued) as a real subprocess call -- never a raw SQL UPDATE, and deliberately
never an in-process call to update_umr_task() (which would bypass mark-umr-terminal's own
structured-evidence gate -- a real, deliberate departure from how resource_governor.py's
older Stage-1 backfill_null_heartbeats() itself writes 'completed', which calls
update_umr_task() directly and therefore predates/bypasses that newer gate). This script
always takes the stricter, CLI-gated path.

Usage:
  python3 reconcile_stale_running_workers.py             # dry run (default, no writes)
  python3 reconcile_stale_running_workers.py --execute   # real writes
"""
import argparse
import json
import os
import subprocess
import sys

import yaml

AI_OS = "/opt/veridian/ai-os"
TASKS_DIR = os.path.join(AI_OS, "tasks")
SCRIPTS = "/opt/veridian/scripts"
SUPERBOSS_REGISTER = os.path.join(SCRIPTS, "superboss-register.py")

# Real, canonical DB-path resolution -- same lazy-import-cached convention
# resource_governor.py's own _superboss_register() established and
# worker-exit-status-bridge.py now also uses (see that module's own comment for the
# full rationale): never a hardcoded path, always the one real SUPERBOSS_REGISTER_DB
# override every other real caller already honors, and hermetically testable against
# a real scratch DB.
_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_stale_reconcile", SUPERBOSS_REGISTER)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()

REPO_LOCAL_PATHS = {
    "compliance-tracker": "/opt/veridian/repos/compliance-tracker",
    "veridian-scripts": "/opt/veridian/repos/veridian-scripts",
    "projexa": "/opt/veridian/repos/projexa",
    "veridian-ai-os": "/opt/veridian/repos/veridian-ai-os",
}
# mark-umr-terminal's own --repo argparse choices (superboss-register.py's p_markterm) --
# a real task.yaml repo outside this set is still handled correctly (--repo-root always
# overrides the path lookup); --repo's value in that case is just a cosmetic placeholder
# for the outputs_json.repo metadata field mark-umr-terminal itself records.
MARK_TERMINAL_REPO_CHOICES = ("compliance-tracker", "veridian-scripts", "projexa")

# Same real vocabulary worker-exit-status-bridge.py and resource_governor.py's own
# _forward_progress_decision() already treat as "a genuinely self-reported negative,
# no-more-automatic-progress outcome" -- reused verbatim so all three never drift apart.
SELF_REPORTED_NEGATIVE_STATUSES = (
    "failed", "blocked", "cancelled", "rejected_duplicate", "superseded", "not_needed",
)


def _run(cmd, timeout=35):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _fetch_affected_rows():
    """Real, read-only sqlite3 query -- the one and only direct DB read this script does;
    every WRITE goes through superboss-register.py's own CLI (see module docstring)."""
    import sqlite3
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT umr_id, unit_name, task_identity, outputs_json, status "
        "FROM umr_tasks WHERE status='running' AND unit_name LIKE 'veridian-worker@%' "
        "ORDER BY umr_id"
    ).fetchall()]
    conn.close()
    return rows


def _unit_active_state(unit_name):
    r = _run(["systemctl", "--user", "show", unit_name, "-p", "ActiveState", "--value"], timeout=10)
    return (r.stdout or "").strip() or "unknown"


def _task_dir_for_row(row):
    """Real task directory resolution, in priority order -- see module docstring."""
    try:
        outputs = json.loads(row.get("outputs_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        outputs = {}
    new_task_id = outputs.get("new_task_id") if isinstance(outputs, dict) else None
    if new_task_id:
        candidate = os.path.join(TASKS_DIR, new_task_id)
        if os.path.isdir(candidate):
            return candidate, "outputs_json.new_task_id"

    task_identity = row.get("task_identity") or ""
    candidate = os.path.join(TASKS_DIR, task_identity)
    if task_identity and os.path.isdir(candidate):
        return candidate, "task_identity (literal TASKS_DIR dir name)"

    umr_suffix = row["umr_id"][4:] if row["umr_id"].upper().startswith("UMR-") else row["umr_id"]
    needle = f"reconcile-umr-{umr_suffix}".lower()
    try:
        names = os.listdir(TASKS_DIR)
    except OSError:
        names = []
    matches = sorted(n for n in names if needle in n.lower())
    if matches:
        # Most recent real match wins -- this codebase's own task-id convention embeds a
        # sortable YYYYMMDD-HHMMSS timestamp prefix, so a plain string sort is a real,
        # deterministic recency order, not a guess.
        return os.path.join(TASKS_DIR, matches[-1]), "reconcile-umr-<id> directory-name match"

    return None, "no task directory found (new_task_id / task_identity / reconcile-umr-<id> all missed)"


def _load_task_yaml(task_dir):
    path = os.path.join(task_dir, "task.yaml")
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None


def _task_ever_had_real_edits(task):
    """Real, cheap (no extra git calls -- pure read of the already-loaded task.yaml doc)
    guard: did ANY checkpoint in this task's own history ever record a non-empty
    files_modified? A branch pushed by a worker that never touched a single file (hard
    stop before any real edit -- preflight rejection, immediate budget exhaustion, etc.)
    has a tip commit that is trivially identical to whatever it forked from, real
    evidence of nothing. Used to gate the ls-remote branch-tip candidate below; the
    recent_commits[0] candidate has its own, stricter status+clean-tree gate (see
    _first_recent_commit_sha) and does not need this one too.

    Deliberately NOT a git-side "commits ahead of origin/main" check: for an ALREADY
    genuinely-merged branch, `merge-base(origin/main, tip) == tip` by definition (that is
    what "merged" means), so any such ahead-of-main math always reads as 0/not-ahead for
    the single most common real 'completed' case -- tried and reverted live against
    UMR-20260807-020846-772f (real task, real merged commit 17b1642, genuinely completed)
    before landing on this simpler, non-git, checkpoint-history-based signal instead."""
    for cp in (task.get("checkpoints") or []):
        if cp.get("files_modified"):
            return True
    return False


def _real_branch_tip_sha(repo, branch):
    """Real remote tip commit sha for `branch` in the shared repo checkout for `repo`, via
    `git ls-remote` -- no local worktree required (the task's own isolated worktree may
    already be pruned by the time this runs; mark-umr-terminal's own commit-existence
    check does its own `git fetch origin <sha>` before verifying, so a sha that only
    exists on the remote is still real, verifiable evidence). None if the repo/branch is
    unresolvable or was never actually pushed."""
    repo_root = REPO_LOCAL_PATHS.get(repo)
    if not repo_root or not os.path.isdir(repo_root) or not branch:
        return None
    try:
        r = _run(["git", "-C", repo_root, "ls-remote", "origin", f"refs/heads/{branch}"], timeout=15)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.split()[0].strip() or None


def _resolve_full_sha(repo_root, abbrev_or_full):
    """Real local resolution of a possibly-abbreviated commit hash (e.g. the 7-char
    hashes `git log --oneline` writes into task.yaml's own recent_commits) to its full
    40-char sha, via `git rev-parse --verify` against the shared repo checkout. None if
    unresolvable there (mark-umr-terminal's own commit_exists_fn does a real `git fetch`
    before its check, so this is a real, additional, purely-local attempt that costs
    nothing when it fails -- the caller still passes the raw value through as a fallback)."""
    if not repo_root or not os.path.isdir(repo_root) or not abbrev_or_full:
        return None
    try:
        r = _run(["git", "-C", repo_root, "rev-parse", "--verify", "--quiet",
                  f"{abbrev_or_full}^{{commit}}"], timeout=15)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.strip()


def _first_recent_commit_sha(task, repo_root):
    """Real candidate sha for a task whose branch has already been deleted (the common
    post-merge-and-clean-up GitHub workflow -- `git ls-remote` then finds nothing even
    though the work is genuinely merged): the top entry of the LAST checkpoint's own
    `recent_commits` (captured live via `git log --oneline -10` at that checkpoint,
    before any branch deletion) is the real HEAD at that moment. Abbreviated (7-char) --
    resolved to a full sha locally when possible, raw abbreviated value returned
    otherwise (mark-umr-terminal's own commit_exists_fn still accepts an abbreviated sha
    once the object is locally present).

    Deliberately gated, real bug found live re-verifying this exact script (task
    task-20260718-164005-cloud-deployment--deployment-automation): a branch that never
    received a real commit of its own has `recent_commits` IDENTICAL to whatever
    origin/main's tip already was at checkpoint time (`git log --oneline` on an
    unmodified branch just shows main's own history) -- that is NOT this task's own
    evidence, it would let a task with a real, still-UNCOMMITTED files_modified list
    (confirmed non-empty for that exact task) get accepted as 'completed' purely because
    main's own unrelated tip commit happens to already be an ancestor of main. Only
    trusted when task.yaml's own last checkpoint status is 'completed' or
    'pending_review' (the worker/review pipeline itself believed real, committed work
    existed) AND that same checkpoint's files_modified is empty (a clean tree -- nothing
    real was left uncommitted). Any other status (blocked/in_progress/failed/...) returns
    None here; those rows fall through to the real-branch-tip (ls-remote) candidate or,
    failing that, to failed/requeue -- never this weaker fallback."""
    checkpoints = task.get("checkpoints") or []
    if not checkpoints:
        return None
    last_cp = checkpoints[-1]
    if last_cp.get("status") not in ("completed", "pending_review"):
        return None
    if last_cp.get("files_modified"):
        return None
    recent = last_cp.get("recent_commits") or []
    if not recent:
        return None
    first_line = (recent[0] or "").strip()
    if not first_line:
        return None
    abbrev = first_line.split()[0]
    return _resolve_full_sha(repo_root, abbrev) or abbrev


def _completion_candidates(task, repo, branch, repo_root):
    """Ordered, real evidence candidates for a completed/completed_unmerged attempt --
    never a guess, each one a real value already recorded somewhere real. First match
    that mark-umr-terminal's own structured-evidence gate accepts wins; caller tries
    them in order.

    The ls-remote branch-tip candidate additionally requires
    _task_ever_had_real_edits(task) -- real, checkpoint-history proof this task actually
    touched a file at some point, not just that a branch got pushed after a genuine
    zero-edit hard stop. The recent_commits[0] candidate has its own, separate, stricter
    gate (status completed/pending_review + a clean tree -- see
    _first_recent_commit_sha's own docstring) and does not need this check too."""
    candidates = []
    evidence = task.get("completion_evidence") or {}
    if evidence.get("commit_sha"):
        candidates.append({"kind": "commit_sha", "value": evidence["commit_sha"],
                            "source": "task.yaml completion_evidence (Rule 7)"})
    if evidence.get("file_path"):
        candidates.append({"kind": "file_path", "value": evidence["file_path"],
                            "source": "task.yaml completion_evidence (Rule 7)"})
    if repo and branch and _task_ever_had_real_edits(task):
        tip = _real_branch_tip_sha(repo, branch)
        if tip:
            candidates.append({"kind": "commit_sha", "value": tip, "source": "git ls-remote (branch still exists)"})
    first_commit = _first_recent_commit_sha(task, repo_root)
    if first_commit:
        candidates.append({"kind": "commit_sha", "value": first_commit,
                            "source": "task.yaml last checkpoint's recent_commits[0] (real `git log` at checkpoint time)"})
    return candidates


def _mark_terminal(umr_id, status, reason, commit_sha=None, file_path=None, repo=None, repo_root=None, pr_number=None):
    cmd = ["python3", SUPERBOSS_REGISTER, "mark-umr-terminal", "--umr-id", umr_id,
           "--status", status, "--reason", reason]
    if commit_sha:
        cmd += ["--commit-sha", commit_sha]
    if file_path:
        cmd += ["--file-path", file_path]
    if repo:
        cmd += ["--repo", repo if repo in MARK_TERMINAL_REPO_CHOICES else "veridian-scripts"]
    if repo_root:
        cmd += ["--repo-root", repo_root]
    if pr_number is not None:
        cmd += ["--pr-number", str(pr_number)]
    r = _run(cmd)
    try:
        parsed = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        parsed = {}
    return r.returncode, parsed, r.stderr


def _requeue(umr_id, reason):
    r = _run(["python3", SUPERBOSS_REGISTER, "reset-umr-to-queued", "--umr-id", umr_id, "--reason", reason])
    try:
        parsed = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        parsed = {}
    return r.returncode, parsed, r.stderr


def decide_and_apply(row, execute):
    umr_id = row["umr_id"]
    unit = row["unit_name"]
    entry = {"umr_id": umr_id, "unit_name": unit, "task_identity": row.get("task_identity")}

    active_state = _unit_active_state(unit)
    entry["active_state"] = active_state
    if active_state not in ("inactive", "failed"):
        # active/activating/deactivating/reloading -- real live or mid-transition unit,
        # not this sweep's business; a future re-run picks it up once settled.
        entry["decision"] = "skipped_not_settled"
        entry["detail"] = f"ActiveState={active_state!r} -- real live/transitional unit, not touched"
        return entry

    task_dir, lookup_method = _task_dir_for_row(row)
    entry["task_dir"] = task_dir
    entry["lookup_method"] = lookup_method

    if task_dir is None:
        reason = (f"reconcile_stale_running_workers.py (STEP 3, task-20260807-052027): unit {unit} "
                  f"confirmed ActiveState={active_state}; {lookup_method} -- genuinely absent evidence, "
                  f"real re-queue so the work is redone (duplicate-safe: same row, status stays one "
                  f"of UMR_ACTIVE_STATUSES).")
        entry["decision"] = "would_requeue" if not execute else "requeued"
        entry["reason"] = reason
        if execute:
            rc, parsed, err = _requeue(umr_id, reason)
            entry["cli_rc"], entry["cli_result"] = rc, parsed
            if rc != 0:
                entry["decision"], entry["cli_stderr"] = "requeue_failed", err[:500]
        return entry

    task = _load_task_yaml(task_dir)
    if task is None:
        reason = (f"reconcile_stale_running_workers.py (STEP 3, task-20260807-052027): unit {unit} "
                  f"confirmed ActiveState={active_state}; real task dir {task_dir!r} found via "
                  f"{lookup_method} but its task.yaml is missing/unreadable -- genuinely absent "
                  f"evidence, real re-queue.")
        entry["decision"] = "would_requeue" if not execute else "requeued"
        entry["reason"] = reason
        if execute:
            rc, parsed, err = _requeue(umr_id, reason)
            entry["cli_rc"], entry["cli_result"] = rc, parsed
            if rc != 0:
                entry["decision"], entry["cli_stderr"] = "requeue_failed", err[:500]
        return entry

    checkpoints = task.get("checkpoints") or []
    last_status = checkpoints[-1]["status"] if checkpoints else task.get("status")
    branch, repo = task.get("branch"), task.get("repo")
    entry["task_yaml_status"], entry["branch"], entry["repo"] = last_status, branch, repo

    repo_root = REPO_LOCAL_PATHS.get(repo)
    candidates = _completion_candidates(task, repo, branch, repo_root)
    entry["completion_candidates"] = candidates

    if candidates:
        base_reason = (f"reconcile_stale_running_workers.py (STEP 3, task-20260807-052027): unit {unit} "
                       f"confirmed ActiveState={active_state}; real task dir {task_dir!r} via "
                       f"{lookup_method}; task.yaml last status={last_status!r}; {len(candidates)} real "
                       f"completion candidate(s) gathered ({', '.join(c['source'] for c in candidates)}) -- "
                       f"letting mark-umr-terminal's own structured-evidence gate decide completed vs "
                       f"completed_unmerged per candidate, never asserted here.")
        if not execute:
            entry["decision"] = "would_attempt_completed_then_completed_unmerged"
            entry["reason"] = base_reason
            return entry

        refusals = []
        accepted = False
        for status_attempt in ("completed", "completed_unmerged"):
            for c in candidates:
                if status_attempt == "completed_unmerged" and c["kind"] != "commit_sha":
                    continue  # completed_unmerged has no file_path evidence path
                kwargs = {"commit_sha": c["value"]} if c["kind"] == "commit_sha" else {"file_path": c["value"]}
                rc, parsed, err = _mark_terminal(umr_id, status_attempt, base_reason, repo=repo,
                                                  repo_root=repo_root, **kwargs)
                if rc == 0:
                    entry["decision"], entry["cli_result"], entry["reason"] = status_attempt, parsed, base_reason
                    entry["accepted_candidate"] = c
                    accepted = True
                    break
                refusals.append({"status": status_attempt, "candidate": c, "refusal": parsed})
            if accepted:
                break
        if accepted:
            return entry
        entry["completion_refusals"] = refusals
        # Real completion candidates existed but no structured gate accepted any of
        # them -- fall through to the same failed/requeue logic below, never silently
        # drop the row.

    if last_status in SELF_REPORTED_NEGATIVE_STATUSES:
        reason = (f"reconcile_stale_running_workers.py (STEP 3, task-20260807-052027): unit {unit} "
                  f"confirmed ActiveState={active_state}; real task dir {task_dir!r} via "
                  f"{lookup_method}; task.yaml's own last checkpoint status={last_status!r} "
                  f"(self-reported, no-more-automatic-progress outcome), no real unmerged commit "
                  f"evidence accepted -- real terminal failed.")
        entry["decision"] = "would_mark_failed" if not execute else "failed"
        entry["reason"] = reason
        if execute:
            rc, parsed, err = _mark_terminal(umr_id, "failed", reason)
            entry["cli_rc"], entry["cli_result"] = rc, parsed
            if rc != 0:
                entry["decision"], entry["cli_stderr"] = "mark_failed_call_failed", err[:500]
        return entry

    reason = (f"reconcile_stale_running_workers.py (STEP 3, task-20260807-052027): unit {unit} "
              f"confirmed ActiveState={active_state}; real task dir {task_dir!r} via {lookup_method}; "
              f"task.yaml's own last checkpoint status={last_status!r}, no real commit evidence "
              f"accepted -- genuinely ambiguous (worker likely killed/crashed mid-work), real "
              f"re-queue so the work is redone rather than left silently stuck.")
    entry["decision"] = "would_requeue" if not execute else "requeued"
    entry["reason"] = reason
    if execute:
        rc, parsed, err = _requeue(umr_id, reason)
        entry["cli_rc"], entry["cli_result"] = rc, parsed
        if rc != 0:
            entry["decision"], entry["cli_stderr"] = "requeue_failed", err[:500]
    return entry


def sweep(execute):
    """Real, callable entry point factored out of main() (UMR-20260813-090037-9a34,
    addendum to UMR-20260806-171945-5767 / STEP 3's own original AUDIT:FAIL: this
    script was a real, tested, one-time sweep with zero periodic caller). Returns the
    exact same report dict main() used to only print -- lets a periodic caller (see
    dispatch-tick.py's own run_stale_running_workers_reconciliation(), the same
    in-process-import wiring convention status-remediation-tick.py already uses for
    reconcile_owner_dispatch_status.py) get real, structured counts back instead of
    re-parsing this script's own stdout JSON. Behavior is byte-for-byte identical to
    the pre-refactor inline main() body -- pure extraction, no new decision logic."""
    rows = _fetch_affected_rows()
    results = [decide_and_apply(row, execute) for row in rows]

    counts = {}
    for e in results:
        counts[e["decision"]] = counts.get(e["decision"], 0) + 1

    return {"execute": execute, "examined": len(rows), "counts": counts, "rows": results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="apply real writes (default: dry run)")
    args = ap.parse_args()

    report = sweep(args.execute)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
